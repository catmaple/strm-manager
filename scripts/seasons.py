#!/usr/bin/env python3
import os, re

TV_ROOT = "/media/tv"
MOVIE_ROOT = "/media/movies"

_CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
       "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})"
# (正则, 是否明确表示"季")。"第N部"只作季号参考,不单独当作剧集的证据
_PATS = [
    (re.compile(r"第\s*" + _NUM + r"\s*季"), True),
    (re.compile(r"第\s*" + _NUM + r"\s*部"), False),
    (re.compile(r"[Ss]eason\s*([0-9]{1,2})"), True),
    (re.compile(r"(?:^|\s)[Ss]([0-9]{1,2})$"), True),
]
_TAIL = r"[\s:：\-—–·、,，]+$"


def cn2int(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        tens = (_CN.get(a) if a else 1)
        ones = (_CN.get(b) if b else 0)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return _CN.get(s)


def clean(s):
    s = re.sub(r"[\[\(（【].*?[\]\)）】]", "", s or "")
    s = re.sub(r'[\\/:*?"<>|]', " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_title(raw):
    """返回 (剧名, 季号, 是否明确标了季)。季号默认 1。"""
    s = raw or ""
    season, explicit = 1, False
    for pat, exp in _PATS:
        m = pat.search(s)
        if m:
            n = cn2int(m.group(1))
            if n and 0 < n < 100:
                season, explicit = n, exp
                s = s[:m.start()] + " " + s[m.end():]
                break
    base = re.sub(_TAIL, "", clean(s)).strip()
    return base, season, explicit


def split_folder(folder):
    m = re.match(r"^(.*?)\s*\((\d{4})\)$", folder)
    return (m.group(1), m.group(2)) if m else (folder, "")


def find_series_dir(base, year, season):
    """已有同名剧集目录就复用;后面的季可以和第一季年份不同。"""
    if os.path.isdir(TV_ROOT):
        for d in sorted(os.listdir(TV_ROOT)):
            b, y = split_folder(d)
            if b != base:
                continue
            if season > 1 or not year or not y or abs(int(y) - int(year)) <= 1:
                return d
    return f"{base} ({year})" if year else base


def is_tv(r):
    if r.get("kind") in {"movie", "tv"}:
        return r["kind"] == "tv"
    return len(r["eps"]) > 1 or bool(r.get("explicit"))


def infer_item_kind(configured_kind, title, item):
    _, season, explicit = parse_title(title)
    if explicit or season > 1:
        return "tv"
    category = str(item.get("type_name") or "").lower()
    tv_tokens = (
        "电视剧", "连续剧", "剧集", "短剧", "网剧", "番剧", "动漫", "综艺",
        "纪录片", "series", "anime",
    )
    movie_tokens = (
        "电影", "影片", "动作片", "喜剧片", "爱情片", "科幻片", "恐怖片",
        "战争片", "犯罪片", "剧情片", "悬疑片", "movie", "film",
    )
    if any(token in category for token in tv_tokens):
        return "tv"
    if any(token in category for token in movie_tokens):
        return "movie"
    for key in ("vod_total", "vod_serial"):
        try:
            if int(item.get(key) or 0) > 1:
                return "tv"
        except (TypeError, ValueError):
            continue
    remarks = str(item.get("vod_remarks") or "").lower()
    if re.search(r"(?:更新至|全|共)\s*\d+\s*集|第\s*\d+\s*集", remarks):
        return "tv"
    play_groups = str(item.get("vod_play_url") or "").split("$$$")
    for group in play_groups:
        episodes = [entry for entry in group.split("#") if "$" in entry]
        if len(episodes) > 1:
            return "tv"
    return configured_kind


def write_strm(r):
    """r: name(剧名/片名), full(带季标记的完整名), season, explicit, year, eps"""
    eps, year = r["eps"], r.get("year", "")
    if not is_tv(r):
        name = r.get("full") or r["name"]
        folder = f"{name} ({year})" if year else name
        d = f"{MOVIE_ROOT}/{folder}"
        os.makedirs(d, exist_ok=True)
        open(f"{d}/{folder}.strm", "w", encoding="utf-8").write(eps[0][1] + "\n")
        return folder, len(eps)
    base, season = r["name"], r.get("season", 1)
    folder = find_series_dir(base, year, season)
    d = f"{TV_ROOT}/{folder}/Season {season:02d}"
    os.makedirs(d, exist_ok=True)
    for i, (_, u) in enumerate(eps, 1):
        open(f"{d}/{base} S{season:02d}E{i:02d}.strm", "w", encoding="utf-8").write(u + "\n")
    return f"{folder}/Season {season:02d}", len(eps)
