#!/usr/bin/env python3
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

import seasons

CONFIG = "/opt/m3u/config.json"
UA = "STRM-Manager/1.0"
MOVIE_TOKENS = (
    "电影", "movie", "movies", "film", "films", "影片", "动作片", "喜剧片",
    "爱情片", "科幻片", "恐怖片", "战争片", "犯罪片", "剧情片", "悬疑片",
)
TV_TOKENS = ("剧", "tv", "series", "anime", "动漫", "综艺", "纪录")


def log(*parts):
    print(*parts, flush=True)


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def category_kinds(api):
    url = api.rstrip("/") + "/?ac=list"
    data = get_json(url)
    kinds = {}
    for item in data.get("class", []):
        name = str(item.get("type_name") or "").lower()
        type_id = str(item.get("type_id") or "")
        if not type_id:
            continue
        if any(token in name for token in MOVIE_TOKENS):
            kinds[type_id] = "movie"
        elif any(token in name for token in TV_TOKENS):
            kinds[type_id] = "tv"
    return kinds


def source_urls(record):
    return {
        url
        for _line, episodes in seasons.parse_play_lines(record, skip_words=())
        for _name, url in episodes
    }


def find_movie_match(title, year, stream_url, sources):
    matches = {}
    search_title, _, explicit = seasons.parse_title(title)
    if not search_title or explicit:
        return None

    for source in sources:
        api = source.get("api", "").strip()
        if not api:
            continue
        try:
            kinds = category_kinds(api)
            query = urllib.parse.urlencode({"ac": "detail", "wd": search_title})
            data = get_json(api.rstrip("/") + "/?" + query)
        except (OSError, ValueError, urllib.error.URLError) as error:
            log(f"查询 CMS 失败 {api}: {error}")
            continue

        for record in data.get("list") or []:
            record_title = str(record.get("vod_name") or "").strip()
            base, season, has_season = seasons.parse_title(record_title)
            record_year = str(record.get("vod_year") or "").strip()
            record_year = record_year if record_year.isdigit() else ""
            if base != search_title or has_season or season != 1:
                continue
            if year and record_year and year != record_year:
                continue
            if str(record.get("type_id") or "") not in kinds:
                continue
            if kinds[str(record.get("type_id"))] != "movie":
                continue
            if stream_url not in source_urls(record):
                continue
            key = (seasons.clean(record_title), record_year or year, stream_url)
            matches[key] = {
                "title": seasons.clean(record_title),
                "year": record_year or year,
            }

    return next(iter(matches.values())) if len(matches) == 1 else None


def movie_candidate_directories(root):
    if not os.path.isdir(root):
        return
    for name in sorted(os.listdir(root)):
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        season_dirs = [
            os.path.join(directory, child)
            for child in os.listdir(directory)
            if re.fullmatch(r"Season\s+\d{2}", child)
            and os.path.isdir(os.path.join(directory, child))
        ]
        if len(season_dirs) != 1 or os.path.basename(season_dirs[0]) != "Season 01":
            continue
        files = [
            os.path.join(season_dirs[0], item)
            for item in os.listdir(season_dirs[0])
            if item.lower().endswith(".strm") and os.path.isfile(os.path.join(season_dirs[0], item))
        ]
        if len(files) != 1:
            continue
        base, year = seasons.split_folder(name)
        parsed, season, explicit = seasons.parse_title(base)
        if not parsed or explicit or season != 1:
            continue
        yield directory, season_dirs[0], files[0], parsed, year


def move_candidate(candidate, match, movie_root):
    series_dir, season_dir, source, _title, _year = candidate
    title = match["title"]
    year = match["year"]
    folder = f"{title} ({year})" if year else title
    destination_dir = os.path.join(movie_root, folder)
    destination = os.path.join(destination_dir, f"{folder}.strm")
    with open(source, encoding="utf-8") as stream_file:
        url = stream_file.read().strip()
    if not url:
        return False, "STRM 内容为空"
    if os.path.exists(destination):
        with open(destination, encoding="utf-8") as existing_file:
            if existing_file.read().strip() == url:
                os.remove(source)
                result = "移除重复项"
            else:
                return False, "电影目录已存在不同的 STRM 文件"
    else:
        os.makedirs(destination_dir, exist_ok=True)
        shutil.move(source, destination)
        result = f"移动到 {destination}"
    for directory in (season_dir, series_dir):
        try:
            os.rmdir(directory)
        except OSError:
            pass
    return True, result


def refresh_jellyfin(config):
    jellyfin = config.get("jellyfin") or {}
    if not jellyfin.get("url") or not jellyfin.get("api_key"):
        return
    request = urllib.request.Request(
        jellyfin["url"].rstrip("/") + "/Library/Refresh",
        method="POST",
        headers={"Authorization": f'MediaBrowser Token="{jellyfin["api_key"]}"'},
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            log("已通知 Jellyfin 扫描媒体库")
    except (OSError, urllib.error.URLError) as error:
        log(f"通知 Jellyfin 扫描失败: {error}")


def main():
    with open(CONFIG, encoding="utf-8") as config_file:
        config = json.load(config_file)
    crawl = config.get("crawl") or {}
    sources = crawl.get("sources") or []
    candidate_count = moved_count = 0
    for candidate in movie_candidate_directories(seasons.TV_ROOT) or []:
        candidate_count += 1
        series_dir, _season_dir, source, title, year = candidate
        with open(source, encoding="utf-8") as stream_file:
            stream_url = stream_file.read().strip()
        match = find_movie_match(title, year, stream_url, sources)
        if not match:
            log(f"保留（无法从 CMS 唯一确认是电影）: {series_dir}")
            continue
        moved, message = move_candidate(candidate, match, seasons.MOVIE_ROOT)
        if moved:
            moved_count += 1
            log(f"{message}: {series_dir}")
        else:
            log(f"保留（{message}）: {series_dir}")
    log(f"自动整理结束：检查 {candidate_count} 个候选，整理 {moved_count} 个，未确认或冲突项已保留")
    if moved_count:
        refresh_jellyfin(config)


if __name__ == "__main__":
    main()
