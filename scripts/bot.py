#!/usr/bin/env python3
import os, re, sys, json, time, subprocess, urllib.request, urllib.parse, urllib.error
import secrets
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, wait
sys.path.insert(0, "/opt/m3u")
import probe
import seasons
import decotv_search

CONFIG = "/opt/m3u/config.json"
UA = "Mozilla/5.0"
SKIP_WORDS = ("预告", "花絮", "特辑")

cfg = {}
TG = {}
ALLOWED = set()
API = ""
cache = {}  # chat_id -> 最近一次搜索的候选列表
action_cache = {}
pending_actions = {}


def get_decotv_cookie():
    decotv = cfg.get("decotv", {})
    username = (decotv.get("admin_username") or "").strip()
    password = decotv.get("admin_password", "")
    if not username or not password:
        cookie = cfg.get("cookie", "")
        if not cookie:
            log("缺少 DecoTV 管理员凭证，无法通过 DecoTV 代理搜索")
        return cookie

    login_url = decotv.get("url", "http://decotv:3000").rstrip("/") + "/api/login"
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        login_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            cookies = response.headers.get_all("Set-Cookie") or []
    except (OSError, urllib.error.URLError) as error:
        log("DecoTV 登录失败:", type(error).__name__)
        return cfg.get("cookie", "")

    cookie = "; ".join(
        part.split(";", 1)[0].strip()
        for part in cookies
        if part.split(";", 1)[0].strip()
    )
    if not cookie:
        log("DecoTV 登录成功但未返回 Cookie")
    return cookie or cfg.get("cookie", "")


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def tg(method, **params):
    req = urllib.request.Request(
        API + method, data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=65) as r:
        return json.load(r)


def say(chat, text, buttons=None):
    p = {"chat_id": chat, "text": text[:4000]}
    if buttons:
        p["reply_markup"] = {"inline_keyboard": buttons}
    try:
        tg("sendMessage", **p)
    except Exception as e:
        log("发送失败:", e)


def http_json(url, cookie=""):
    headers = {"User-Agent": UA}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def get_decotv_sources(cookie):
    return decotv_search.get_sources(cfg, cookie, http_json, log)


def parse_search_title(raw):
    base, season, explicit = seasons.parse_title(raw)
    episode_match = re.search(
        r"(?:第\s*([0-9]{1,4}|[一二三四五六七八九十两]{1,3})\s*[集话話期]"
        r"|(?:EP|E)\s*0*([0-9]{1,4}))\s*$",
        base,
        re.IGNORECASE,
    )
    if not episode_match:
        return base, season, explicit, False
    base = re.sub(r"[\s:：\-—–·、,，]+$", "", base[:episode_match.start()]).strip()
    return base, season, explicit, True


def _looks_like_url(value):
    value = str(value or "").strip().lower()
    return bool(value) and (
        value.startswith("http://")
        or value.startswith("https://")
        or "/api/" in value
        or "api." in value
        or ".m3u8" in value
        or ".com" in value
        or ".net" in value
        or ".org" in value
    )


def _route_label(source_name, line_name, fallback_host=""):
    source = str(source_name or "").strip()
    line = str(line_name or "").strip()
    if source and line and not _looks_like_url(line):
        return f"{source}/{line}"
    if source:
        return source
    if line and _looks_like_url(line):
        return fallback_host or "DecoTV"
    if line:
        return line
    return fallback_host or "DecoTV"


def candidates(title):
    tbase, tseason, _, _ = parse_search_title(title)
    decotv = cfg.get("decotv", {})
    if (decotv.get("admin_username") or "").strip() and decotv.get("admin_password"):
        return decotv_page_candidates(tbase)
    return cms_candidates(tbase, tseason)


def decotv_page_candidates(title):
    decotv_url = (cfg.get("decotv", {}).get("url") or "http://decotv:3000").rstrip("/")
    query = urllib.parse.urlencode({"q": title, "adfilter": "direct"})
    cookie = get_decotv_cookie()
    try:
        payload = http_json(decotv_url + "/api/search?" + query, cookie)
    except Exception as error:
        log("DecoTV 聚合搜索失败:", type(error).__name__)
        raise ValueError("无法读取 DecoTV 聚合搜索结果，请检查管理员凭证和 DecoTV 服务状态") from error

    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("DecoTV 聚合搜索返回格式异常")

    results = []
    seen = set()
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("title") or "").strip()
        base, item_season, explicit, episode_marked = parse_search_title(raw)
        source = str(item.get("source") or "").strip()
        item_id = str(item.get("id") or "").strip()
        urls = item.get("episodes")
        episode_identity = (
            tuple(str(url) for url in urls) if isinstance(urls, list) else ()
        )
        identity = (
            (source, item_id)
            if source and item_id
            else (
                source,
                seasons.clean(raw).casefold(),
                str(item.get("year") or ""),
                episode_identity,
            )
        )
        if not raw or identity in seen:
            continue
        seen.add(identity)

        tags = raw + str(item.get("type_name") or "") + str(item.get("remarks") or "")
        if any(word in tags for word in SKIP_WORDS) or not base:
            continue
        if not isinstance(urls, list):
            continue
        titles = item.get("episodes_titles")
        if not isinstance(titles, list):
            titles = []
        eps = [
            (
                str(titles[index]).strip()
                if index < len(titles) and titles[index]
                else str(index + 1),
                url.strip(),
            )
            for index, url in enumerate(urls)
            if isinstance(url, str) and url.strip() and ".m3u8" in url.lower()
        ]
        if not eps:
            continue
        year_value = str(item.get("year") or "")
        year_match = re.search(r"\d{4}", year_value)
        year = year_match.group(0) if year_match else ""
        kind = seasons.infer_item_kind(
            "movie",
            raw,
            {
                "type_name": item.get("type_name"),
                "vod_remarks": item.get("remarks"),
                "vod_total": len(eps),
            },
        )
        line_label = _route_label(
            item.get("source_name") or source or "DecoTV",
            None,
            "DecoTV",
        )
        results.append({
            "name": base,
            "full": seasons.clean(raw),
            "season": item_season,
            "explicit": explicit,
            "year": year,
            "kind": "tv" if kind == "tv" or episode_marked else kind,
            "lang": item.get("lang") or "",
            "lines": [{"label": line_label, "eps": eps}],
            "eps": eps,
            "source": source,
            "id": item_id,
        })

    return results


def cms_candidates(tbase, tseason):
    out = {}
    decotv = cfg.get("decotv", {})
    decotv_url = (decotv.get("url") or "http://decotv:3000").rstrip("/")
    cookie = get_decotv_cookie()
    sources = get_decotv_sources(cookie)
    for source in sources:
        api = source["api"]
        query = urllib.parse.urlencode({"ac": "detail", "wd": tbase})
        target = f"{api}{'&' if '?' in api else '?'}{query}"
        url = decotv_url + "/api/proxy/cms?url=" + urllib.parse.quote(target, safe="")
        host = urllib.parse.urlparse(api).netloc
        log("Telegram 搜索来源:", source.get("name") or host, host)
        try:
            data = http_json(url, cookie)
        except Exception as e:
            log("搜索失败:", host, type(e).__name__)
            continue
        items = data.get("list", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            log("CMS 搜索响应格式异常:", host)
            continue
        log("CMS 搜索返回记录数:", host, len(items))
        added_from_source = 0
        for v in items:
            raw = v.get("vod_name", "")
            tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
            if any(k in tags for k in SKIP_WORDS):
                continue
            base, sn, explicit, episode_marked = parse_search_title(raw)
            if not base:
                continue
            vy = (v.get("vod_year") or "").strip()
            vy = vy if vy.isdigit() else ""
            groups = v.get("vod_play_url", "").split("$$$")
            froms = v.get("vod_play_from", "").split("$$$")
            record_lines = []
            for i, g in enumerate(groups):
                eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
                eps = [
                    (n, u.strip())
                    for n, u in eps
                    if ".m3u8" in u.lower()
                ]
                if not eps:
                    continue
                ln = froms[i] if i < len(froms) else f"线路{i}"
                record_lines.append((ln, eps))
            if not record_lines:
                continue
            added_from_source += 1
            kind = seasons.infer_item_kind("movie", raw, v)
            c = out.setdefault((base, sn, vy), {
                "name": base, "full": seasons.clean(raw), "season": sn,
                "explicit": explicit, "year": vy,
                "kind": kind,
                "lang": v.get("vod_lang") or "", "lines": [],
                "record_count": 0, "episode_marked": False})
            c["record_count"] += 1
            c["episode_marked"] = c["episode_marked"] or episode_marked
            if kind == "tv" or episode_marked:
                c["kind"] = "tv"
            source_name = source.get("name") or host
            for ln, eps in record_lines:
                line_label = _route_label(source_name, ln, host)
                line = next(
                    (entry for entry in c["lines"] if entry["label"] == line_label),
                    None,
                )
                if line is None:
                    line = {"label": line_label, "eps": []}
                    c["lines"].append(line)
                existing_urls = {url for _name, url in line["eps"]}
                for episode_name, episode_url in eps:
                    if episode_url not in existing_urls:
                        line["eps"].append((episode_name, episode_url))
                        existing_urls.add(episode_url)
        log("CMS 搜索可用记录数:", host, added_from_source)
    res = list(out.values())
    season_groups = {}
    for c in res:
        season_groups.setdefault((c["name"], c["year"]), set()).add(c["season"])
    for c in res:
        if (
            (c["episode_marked"] and c["record_count"] > 1)
            or len(season_groups[(c["name"], c["year"])]) > 1
        ):
            c["kind"] = "tv"
    for c in res:
        c["eps"] = max(c["lines"], key=lambda l: len(l["eps"]))["eps"]  # 仅用于显示
        c.pop("record_count", None)
        c.pop("episode_marked", None)
    res.sort(key=lambda c: not (c["name"] == tbase and c["season"] == tseason))
    return res[:8]


def label(c):
    tv = seasons.is_tv({"eps": c["eps"], "explicit": c["explicit"], "kind": c["kind"]})
    kind = f"第{c['season']}季 · {len(c['eps'])}集" if tv else "电影"
    nm = c["name"] if tv else c["full"]
    year = f" ({c['year']})" if c["year"] else ""
    lang = f" · {c.get('lang')}" if c.get("lang") else ""
    return f"{nm}{year} · {kind}{lang}"


def _short_probe_info(info):
    # "1920x1080 / 12.5Mbps/需2.1, 0.31s" -> "1920x1080 · 0.31s"
    info = str(info or "")
    first_byte = re.search(r"([\d.]+s)\s*$", info)
    resolution = info.split(" / ", 1)[0] if " / " in info else ""
    if resolution and "Mbps" in resolution:
        resolution = ""
    parts = [part for part in (resolution, first_byte.group(1) if first_byte else "") if part]
    return " · ".join(parts) if parts else "测速失败"


PROBE_MAX_CANDIDATES = 12
PROBE_MAX_LINES = 3
PROBE_DEADLINE = 40


def annotate_search_results(cands):
    """测速并剔除全部线路都失败的结果，其余按可用线路排序；总耗时有上限。"""
    def probe_candidate(candidate):
        lines = (candidate.get("lines") or [])[:PROBE_MAX_LINES]
        if not lines:
            return None
        try:
            return probe.rank([dict(line) for line in lines], log)
        except Exception as error:
            log("搜索结果测速异常:", type(error).__name__)
            return None

    pool = ThreadPoolExecutor(max_workers=min(8, max(1, len(cands))))
    futures = [pool.submit(probe_candidate, candidate) for candidate in cands]
    wait(futures, timeout=PROBE_DEADLINE)
    pool.shutdown(wait=False, cancel_futures=True)
    usable = []
    for candidate, future in zip(cands, futures):
        ranked = future.result() if future.done() and not future.cancelled() else None
        if not ranked:
            continue
        candidate["lines"] = ranked
        candidate["_probe"] = _short_probe_info(ranked[0].get("_info"))
        usable.append(candidate)
    cands[:] = usable


def search_result_button_label(candidate):
    title = label(candidate)
    lines = candidate.get("lines") or []
    source = str(lines[0].get("label") or "") if lines else ""
    prefix = f"{candidate['_probe']} · " if candidate.get("_probe") else ""
    budget = 60 - len(prefix)
    if not source:
        return (prefix + title)[:60]
    title_limit = max(8, budget - len(source) - 3)
    if len(title) > title_limit:
        title = title[:title_limit - 1] + "…"
    return f"{prefix}{title} · {source}"[:60]


def find_library_items(title):
    query_base, query_season, explicit = seasons.parse_title(title)
    query_clean = seasons.clean(title).casefold()
    matches = []

    if os.path.isdir(seasons.MOVIE_ROOT):
        for folder in sorted(os.listdir(seasons.MOVIE_ROOT)):
            path = os.path.join(seasons.MOVIE_ROOT, folder)
            if not os.path.isdir(path):
                continue
            base, year = seasons.split_folder(folder)
            if (
                seasons.clean(folder).casefold() != query_clean
                and seasons.clean(base).casefold() != query_base.casefold()
            ):
                continue
            files = [
                os.path.join(path, name)
                for name in sorted(os.listdir(path))
                if name.lower().endswith(".strm")
                and os.path.isfile(os.path.join(path, name))
            ]
            if files:
                matches.append({
                    "kind": "movie",
                    "name": base,
                    "full": folder,
                    "year": year,
                    "season": 1,
                    "path": path,
                    "files": files,
                })

    if os.path.isdir(seasons.TV_ROOT):
        for folder in sorted(os.listdir(seasons.TV_ROOT)):
            series_path = os.path.join(seasons.TV_ROOT, folder)
            if not os.path.isdir(series_path):
                continue
            base, year = seasons.split_folder(folder)
            if seasons.clean(base).casefold() != query_base.casefold():
                continue
            for season_folder in sorted(os.listdir(series_path)):
                season_match = re.fullmatch(r"Season\s+(\d{1,2})", season_folder, re.I)
                if not season_match:
                    continue
                season = int(season_match.group(1))
                if explicit and season != query_season:
                    continue
                season_path = os.path.join(series_path, season_folder)
                files = [
                    os.path.join(root, name)
                    for root, dirs, names in os.walk(season_path)
                    for name in sorted(names)
                    if name.lower().endswith(".strm")
                ]
                files.sort()
                if files:
                    matches.append({
                        "kind": "tv",
                        "name": base,
                        "full": folder,
                        "year": year,
                        "season": season,
                        "path": season_path,
                        "files": files,
                    })
    return matches


def item_label(item):
    year = f" ({item['year']})" if item.get("year") else ""
    if item["kind"] == "tv":
        return f"{item['name']}{year} · 第{item['season']}季 · {len(item['files'])}集"
    return f"{item['name']}{year} · 电影"


def show_delete_options(chat, title):
    items = find_library_items(title)
    if not items:
        say(chat, f"媒体库中没有找到“{title}”对应的 STRM。")
        return
    token = secrets.token_hex(4)
    action_cache[chat] = {"token": token, "items": items}
    buttons = [
        [{"text": item_label(item)[:60], "callback_data": f"delitem:{token}:{index}"}]
        for index, item in enumerate(items)
    ]
    say(chat, "请选择要删除的媒体：", buttons)


def confirm_delete(chat, item):
    token = secrets.token_hex(4)
    pending_actions[chat] = {"action": "delete", "item": item, "token": token}
    say(
        chat,
        f"确认删除 {item_label(item)}？将删除该条目的 {len(item['files'])} 个 STRM 文件，以及所在目录中的海报、NFO 等全部文件，此操作不可恢复。",
        [[
            {"text": "确认删除", "callback_data": f"delconfirm:{token}"},
            {"text": "取消", "callback_data": f"cancel:{token}"},
        ]],
    )


def _inside_root(path, root):
    resolved_path = os.path.realpath(path)
    resolved_root = os.path.realpath(root)
    try:
        return os.path.commonpath((resolved_path, resolved_root)) == resolved_root
    except ValueError:
        return False


def _remove_tree(directory, root):
    real_dir = os.path.realpath(directory)
    real_root = os.path.realpath(root)
    if real_dir == real_root or not _inside_root(directory, root):
        raise ValueError("拒绝删除媒体库目录以外的内容")
    if os.path.isdir(directory):
        shutil.rmtree(directory)


def delete_item(item):
    root = seasons.MOVIE_ROOT if item["kind"] == "movie" else seasons.TV_ROOT
    for path in item["files"]:
        if not _inside_root(path, root) or not path.lower().endswith(".strm"):
            raise ValueError("拒绝删除媒体库目录以外的文件")
    deleted = sum(1 for path in item["files"] if os.path.isfile(path))

    # 电影目录和剧集的季目录只属于该条目，连同海报、NFO 等一并删除
    _remove_tree(item["path"], root)
    if item["kind"] == "tv":
        series_dir = os.path.dirname(item["path"])
        remaining = [
            name for name in os.listdir(series_dir)
            if re.fullmatch(r"Season\s+\d{1,2}", name, re.I)
            and os.path.isdir(os.path.join(series_dir, name))
        ] if os.path.isdir(series_dir) else []
        if os.path.isdir(series_dir):
            if remaining:
                season_prefix = f"season{int(item['season']):02d}-"
                for name in os.listdir(series_dir):
                    path = os.path.join(series_dir, name)
                    if name.lower().startswith(season_prefix) and os.path.isfile(path):
                        os.remove(path)
            else:
                _remove_tree(series_dir, root)
    return deleted


def _read_strm_url(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _line_urls(line):
    return {url for _name, url in (line.get("series_eps") or line.get("eps") or [])}


def show_edit_sources(chat, item):
    query = item["name"]
    if item["kind"] == "tv":
        query += f" 第{item['season']}季"
    try:
        results = candidates(query)
    except ValueError as error:
        say(chat, str(error))
        return
    line_choices = []
    skipped_incomplete = 0
    for result in results:
        if result["name"].casefold() != item["name"].casefold():
            continue
        if result["season"] != item["season"]:
            continue
        if (
            item.get("year")
            and result.get("year")
            and abs(int(item["year"]) - int(result["year"])) > 1
        ):
            continue
        for line in result["lines"]:
            episode_urls = line["eps"]
            if item["kind"] == "tv":
                if len(episode_urls) < len(item["files"]):
                    skipped_incomplete += 1
                    continue
                choice_eps = episode_urls[:1]
            else:
                choice_eps = episode_urls
            if item["kind"] == "movie" and not line["eps"]:
                continue
            choice = {
                **line,
                "eps": choice_eps,
                "series_eps": episode_urls if item["kind"] == "tv" else None,
                "name": result["name"],
                "full": result["full"],
                "season": result["season"],
                "explicit": result["explicit"],
                "year": result["year"],
                "kind": result["kind"],
            }
            line_choices.append(choice)
    if not line_choices:
        say(
            chat,
            "没有找到集数/年份与本地条目匹配的可用播放线路；为避免覆盖不完整的剧集，未提供换源选项。",
        )
        return
    say(chat, "正在测速可用线路，可能需要一些时间……")
    ranked = probe.rank(line_choices, log)
    if not ranked:
        say(chat, "所有候选线路测速失败，暂不提供换源。")
        return
    token = secrets.token_hex(4)
    pending_actions[chat] = {
        "action": "edit",
        "item": item,
        "lines": ranked,
        "token": token,
    }
    current_url = _read_strm_url(item["files"][0]) if item.get("files") else ""
    current_index = next(
        (
            index for index, line in enumerate(ranked[:20])
            if current_url and current_url in _line_urls(line)
        ),
        None,
    )
    buttons = [
        [{
            "text": (
                ("✅当前 · " if index == current_index else "")
                + f"{line.get('_info', '')} · {line.get('label', '线路')}"
            )[:60],
            "callback_data": f"editsrc:{token}:{index}",
        }]
        for index, line in enumerate(ranked[:20])
    ]
    note = ""
    if current_index is None:
        note = "\n（当前使用的线路不在可用列表中，可能已失效或测速失败）"
    say(chat, f"请选择要用于 {item_label(item)} 的播放线路：{note}", buttons)
    if skipped_incomplete:
        say(chat, f"另有 {skipped_incomplete} 条线路集数少于本地 {len(item['files'])} 集，已隐藏以免覆盖不完整。")


def update_item_source(item, line):
    episode_urls = line.get("series_eps") or line["eps"]
    urls = [url for _name, url in episode_urls]
    if item["kind"] == "movie":
        if not urls:
            raise ValueError("所选线路没有可写入的播放地址")
        updates = [(item["files"][0], urls[0])]
    else:
        if len(urls) < len(item["files"]):
            raise ValueError("所选线路集数少于本地 STRM 文件数，已取消更新")
        updates = list(zip(item["files"], urls))

    staged = []
    try:
        for path, url in updates:
            root = seasons.MOVIE_ROOT if item["kind"] == "movie" else seasons.TV_ROOT
            if (
                not _inside_root(path, root)
                or not path.lower().endswith(".strm")
                or not os.path.isfile(path)
            ):
                raise ValueError("本地 STRM 文件已不存在或位于媒体库目录之外")
            descriptor, temporary = tempfile.mkstemp(
                prefix=".strm-", suffix=".tmp", dir=os.path.dirname(path)
            )
            staged.append((temporary, path))
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(url + "\n")
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _path in staged:
            if os.path.exists(temporary):
                os.remove(temporary)
    return len(updates)


def run_season_migration(chat, apply=False):
    command = [sys.executable, "/opt/m3u/migrate_seasons.py"]
    if apply:
        command.append("--apply")
    try:
        result = subprocess.run(
            command,
            cwd="/opt/m3u",
            capture_output=True,
            text=True,
            timeout=1800 if apply else 120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        say(chat, "季目录迁移超时；请检查 STRM Manager 日志和媒体目录。")
        return
    except OSError as error:
        say(chat, f"无法启动季目录迁移：{error}")
        return

    output = "\n".join(
        line for line in (result.stdout + "\n" + result.stderr).splitlines() if line.strip()
    )
    if result.returncode != 0:
        say(chat, f"季目录迁移失败（退出码 {result.returncode}）：\n{output[-3000:] or '无输出'}")
        return

    if apply:
        say(chat, f"季目录迁移执行完成：\n{output[-3000:] or '脚本无输出'}")
        if re.search(r"共\s*[1-9]\d*\s*个季目录", output):
            refresh_jellyfin()
        pending_actions.pop(chat, None)
        return

    say(chat, f"季目录迁移预览（尚未修改文件）：\n{output[-3000:] or '脚本无输出'}")
    match = re.search(r"共\s*(\d+)\s*个季目录待迁移", output)
    if not match or int(match.group(1)) == 0:
        return

    token = secrets.token_hex(4)
    pending_actions[chat] = {"action": "migrate_seasons", "token": token}
    say(
        chat,
        f"预览发现 {match.group(1)} 个季目录待迁移。确认后会移动 STRM 文件并整理目录结构，是否继续？",
        [[
            {"text": "确认迁移", "callback_data": f"migrateapply:{token}"},
            {"text": "取消", "callback_data": f"cancel:{token}"},
        ]],
    )


def refresh_jellyfin():
    jf = cfg.get("jellyfin", {})
    if not jf.get("api_key"):
        return False
    req = urllib.request.Request(
        jf["url"].rstrip("/") + "/Library/Refresh", method="POST",
        headers={"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        log("通知 Jellyfin 失败:", e)
        return False


def do_add(chat, c, line):
    folder, _ = seasons.write_strm({
        "name": c["name"], "full": c["full"], "season": c["season"],
        "explicit": c["explicit"], "year": c["year"], "kind": c["kind"],
        "eps": line["eps"]})
    ok = refresh_jellyfin()
    tail = "已通知 Jellyfin 扫描,稍等一两分钟在 VidHub 里刷新。" if ok \
        else "未配置 Jellyfin api_key,请等它定时扫描或手动扫描。"
    say(chat, f"✅ 已加入:{label(c)}\n线路:{line.get('label')}({line.get('_info', '')})\n目录:{folder}\n{tail}")


def show_add_sources(chat, c):
    say(chat, "正在测速可用线路，请稍候……")
    ranked = probe.rank(c["lines"], log)
    if not ranked:
        say(chat, "所有候选线路测速失败，暂不能入库。")
        return
    token = secrets.token_hex(4)
    pending_actions[chat] = {
        "action": "add",
        "candidate": c,
        "lines": ranked,
        "token": token,
    }
    buttons = [
        [{
            "text": _add_source_button_label(line),
            "callback_data": f"addline:{token}:{index}",
        }]
        for index, line in enumerate(ranked[:20])
    ]
    say(chat, "请选择要入库的播放线路（清晰度、网速、首包响应时间和来源）：", buttons)


def _add_source_button_label(line):
    info = str(line.get("_info") or "测速信息未知")
    source = str(line.get("label") or "线路")
    source_limit = max(12, 60 - len(info) - 3)
    if len(source) > source_limit:
        source = source[:source_limit - 1] + "…"
    return f"{info} · {source}"[:60]


def search_and_reply(chat, title, direct=False):
    say(chat, f"正在搜索:{title}")
    try:
        cands = candidates(title)
    except ValueError as error:
        say(chat, str(error))
        return
    if not cands:
        say(chat, "没找到可用的 m3u8 资源,换个关键词或换个源试试。")
        return
    tb, ts, _, _ = parse_search_title(title)
    exact = [c for c in cands if c["name"] == tb and c["season"] == ts]
    if direct and len(exact) == 1:
        show_add_sources(chat, exact[0])
        return
    # 结果过多时优先保留标题完全匹配的，避免测速耗时过长和按钮超限
    cands = (exact + [c for c in cands if c not in exact])[:PROBE_MAX_CANDIDATES]
    cache[chat] = cands
    say(chat, f"正在测速 {len(cands)} 个搜索结果，请稍候……")
    try:
        annotate_search_results(cands)
    except Exception as error:
        log("搜索结果测速失败:", type(error).__name__, error)
        say(chat, f"测速出错：{type(error).__name__}，请稍后重试。")
        return
    if not cands:
        say(chat, "找到的播放源全部测速失败，暂无可用资源，请稍后重试或换个关键词。")
        return
    buttons = [[{"text": search_result_button_label(c), "callback_data": f"add:{i}"}]
               for i, c in enumerate(cands)]
    say(chat, "请选择要加入的版本:", buttons)


def run_script(chat, script, extra, title):
    say(chat, f"开始运行{title},可能需要几分钟……")
    try:
        r = subprocess.run([sys.executable, script] + extra,
                           capture_output=True, text=True, timeout=3600)
        lines = (r.stdout + r.stderr).strip().splitlines()
        say(chat, "完成:\n" + "\n".join(lines[-12:]))
    except Exception as e:
        say(chat, f"运行失败:{e}")


def handle(u):
    if "callback_query" in u:
        cq = u["callback_query"]
        chat = cq["message"]["chat"]["id"]
        if chat not in ALLOWED:
            return
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"])
        except Exception:
            pass
        data = cq.get("data", "")
        if data.startswith("add:"):
            cands = cache.get(chat) or []
            i = int(data[4:])
            if i < len(cands):
                show_add_sources(chat, cands[i])
            else:
                say(chat, "列表已过期,请重新发送片名搜索。")
        elif data.startswith("addline:"):
            pending = pending_actions.get(chat) or {}
            parts = data.split(":")
            lines = pending.get("lines") or []
            try:
                if (
                    len(parts) != 3
                    or pending.get("action") != "add"
                    or parts[1] != pending.get("token")
                ):
                    raise ValueError
                line = lines[int(parts[2])]
            except (IndexError, ValueError):
                say(chat, "线路列表已过期，请重新发送片名搜索。")
                return
            try:
                do_add(chat, pending["candidate"], line)
            except (KeyError, OSError, ValueError) as error:
                say(chat, f"入库失败：{error}")
            finally:
                pending_actions.pop(chat, None)
        elif data.startswith("delitem:"):
            parts = data.split(":")
            selection = action_cache.get(chat) or {}
            try:
                if len(parts) != 3 or parts[1] != selection.get("token"):
                    raise ValueError
                item = selection["items"][int(parts[2])]
            except (IndexError, ValueError):
                say(chat, "列表已过期，请重新发送 /delete 片名。")
            else:
                confirm_delete(chat, item)
        elif data.startswith("delconfirm:"):
            pending = pending_actions.get(chat) or {}
            if (
                pending.get("action") != "delete"
                or data.split(":", 1)[1] != pending.get("token")
            ):
                say(chat, "删除确认已过期，请重新发送 /delete 片名。")
                return
            try:
                count = delete_item(pending["item"])
            except (OSError, ValueError) as error:
                say(chat, f"删除失败：{error}")
            else:
                say(chat, f"已删除 {count} 个 STRM 文件。")
                refresh_jellyfin()
            pending_actions.pop(chat, None)
        elif data.startswith("edititem:"):
            parts = data.split(":")
            selection = action_cache.get(chat) or {}
            try:
                if len(parts) != 3 or parts[1] != selection.get("token"):
                    raise ValueError
                item = selection["items"][int(parts[2])]
            except (IndexError, ValueError):
                say(chat, "列表已过期，请重新发送 /edit 片名。")
            else:
                show_edit_sources(chat, item)
        elif data.startswith("editsrc:"):
            pending = pending_actions.get(chat) or {}
            lines = pending.get("lines") or []
            parts = data.split(":")
            try:
                if (
                    len(parts) != 3
                    or pending.get("action") != "edit"
                    or parts[1] != pending.get("token")
                ):
                    raise ValueError
                line = lines[int(parts[2])]
            except (IndexError, ValueError):
                say(chat, "线路列表已过期，请重新发送 /edit 片名。")
                return
            try:
                count = update_item_source(pending["item"], line)
            except (KeyError, OSError, ValueError) as error:
                say(chat, f"换源失败：{error}")
            else:
                first_url = (line.get("series_eps") or line["eps"])[0][1]
                say(
                    chat,
                    f"已更新 {count} 个 STRM 文件。\n线路：{line.get('label')}\n"
                    f"测速：{line.get('_info', '未知')}\n首集地址：{first_url}",
                )
                refresh_jellyfin()
            pending_actions.pop(chat, None)
        elif data.startswith("migrateapply:"):
            pending = pending_actions.get(chat) or {}
            if (
                pending.get("action") != "migrate_seasons"
                or data.split(":", 1)[1] != pending.get("token")
            ):
                say(chat, "迁移确认已过期，请重新发送 /migrate 预览。")
                return
            run_season_migration(chat, apply=True)
        elif data.startswith("cancel:"):
            pending = pending_actions.get(chat) or {}
            if data.split(":", 1)[1] == pending.get("token"):
                pending_actions.pop(chat, None)
                say(chat, "已取消。")
            else:
                say(chat, "操作已过期。")
        return

    m = u.get("message")
    if not m:
        return
    chat = m["chat"]["id"]
    if chat not in ALLOWED:
        log("忽略未授权用户:", chat)
        return
    text = (m.get("text") or "").strip()
    if not text:
        return

    if text in ("/start", "/help"):
        say(
            chat,
            "可用功能：\n"
            "发送片名：搜索 DecoTV 全部已启用来源，选择版本和播放线路后入库\n"
            "/add 片名：同名唯一时跳过版本选择，但仍可选择播放线路\n"
            "/delete 片名：删除本地 STRM；选择条目并二次确认\n"
            "/edit 片名：测速可用线路，显示速度/分辨率并切换播放源\n"
            "/migrate：预览并确认迁移旧剧集季目录\n"
            "/crawl：运行一次视频源分类抓取\n"
            "/auto：运行一次 DecoTV 榜单抓取\n"
            "/recheck：复测已保存线路\n"
            "/stats 或 /status：查看媒体库和抓取状态\n"
            "/scan：通知 Jellyfin 扫描媒体库",
        )
    elif text.startswith("/add"):
        title = text[4:].strip()
        if title:
            search_and_reply(chat, title, direct=True)
        else:
            say(chat, "用法:/add 片名")
    elif text.startswith("/delete"):
        pending_actions.pop(chat, None)
        title = text[len("/delete"):].strip()
        if title:
            show_delete_options(chat, title)
        else:
            say(chat, "用法:/delete 片名")
    elif text.startswith("/edit"):
        pending_actions.pop(chat, None)
        title = text[len("/edit"):].strip()
        if not title:
            say(chat, "用法:/edit 片名")
            return
        items = find_library_items(title)
        if not items:
            say(chat, f"媒体库中没有找到“{title}”对应的 STRM。")
        elif len(items) == 1:
            show_edit_sources(chat, items[0])
        else:
            token = secrets.token_hex(4)
            action_cache[chat] = {"token": token, "items": items}
            buttons = [
                [{
                    "text": item_label(item)[:60],
                    "callback_data": f"edititem:{token}:{index}",
                }]
                for index, item in enumerate(items)
            ]
            say(chat, "找到多个本地条目，请选择要换源的条目：", buttons)
    elif text == "/migrate":
        pending_actions.pop(chat, None)
        run_season_migration(chat)
    elif text == "/crawl":
        run_script(chat, "/opt/m3u/crawl.py", ["--new", "30"], "批量抓取")
    elif text == "/auto":
        run_script(chat, "/opt/m3u/auto.py", [], "榜单抓取")
    elif text == "/recheck":
        run_script(chat, "/opt/m3u/recheck.py", [], "线路复测")
    elif text in ("/stats", "/status"):
        try:
            r = subprocess.run([sys.executable, "/opt/m3u/stats.py", "--no-send"],
                               capture_output=True, text=True, timeout=120)
            say(chat, (r.stdout or r.stderr).strip() or "无输出")
        except Exception as e:
            say(chat, f"统计失败:{e}")
    elif text == "/scan":
        say(chat, "已通知 Jellyfin 扫描" if refresh_jellyfin() else "通知失败或未配置 api_key")
    elif text.startswith("/"):
        say(chat, "未知命令,发送 /help 查看用法。")
    else:
        search_and_reply(chat, text)


def main():
    global cfg, TG, ALLOWED, API
    with open(CONFIG, encoding="utf-8") as config_file:
        cfg = json.load(config_file)
    TG = cfg.get("telegram", {})
    ALLOWED = {int(x) for x in TG.get("allowed_chat_ids", [])}
    API = f"https://api.telegram.org/bot{TG.get('token', '')}/"
    log("机器人启动,授权用户:", sorted(ALLOWED))
    offset = None
    while True:
        params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            res = tg("getUpdates", **params)
        except Exception as e:
            log("getUpdates 失败:", e)
            time.sleep(5)
            continue
        for u in res.get("result", []):
            offset = u["update_id"] + 1
            try:
                handle(u)
            except Exception as e:
                log("处理出错:", e)


if __name__ == "__main__":
    main()
