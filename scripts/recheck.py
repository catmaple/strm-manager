#!/usr/bin/env python3
import os, re, sys, json, time, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, "/opt/m3u")
import probe
import seasons
import decotv_search

CONFIG = "/opt/m3u/config.json"
HEALTH = "/opt/m3u/health.json"
UA = "Mozilla/5.0"
SKIP_WORDS = ("预告", "花絮", "特辑")

DRY = "--dry" in sys.argv
OPT = "--optimize" in sys.argv
FORCE = "--force" in sys.argv
ONLY = None
if "--only" in sys.argv:
    i = sys.argv.index("--only")
    ONLY = sys.argv[i + 1] if i + 1 < len(sys.argv) else None

cfg = json.load(open(CONFIG, encoding="utf-8"))
rc = cfg.get("recheck", {})
MIN_HR = rc.get("min_headroom", 1.5)      # 低于此余量视为"太慢"
GAIN = rc.get("switch_gain", 1.3)         # 新线路至少好这么多倍才切换
OK_DAYS = rc.get("interval_days", 7)      # 健康的片多久复查一次
MAX_RUN = rc.get("max_per_run", 40)       # 每次最多检查几部
health = json.load(open(HEALTH, encoding="utf-8")) if os.path.exists(HEALTH) else {}


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def http_json(url, cookie=""):
    headers = {"User-Agent": UA}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def fmt(s):
    if not s:
        return "失败/不可用"
    return f"{s['mbps']:.1f}Mbps/需{s['need']:.1f} (余量{s['headroom']:.1f}x, 首包{s['ttfb']:.2f}s)"


def route_label(source_name, line_name, fallback_host=""):
    source = str(source_name or "").strip()
    line = str(line_name or "").strip()
    value = line.lower()
    looks_like_url = (
        value.startswith("http://")
        or value.startswith("https://")
        or "/api/" in value
        or "api." in value
        or ".m3u8" in value
        or ".com" in value
        or ".net" in value
        or ".org" in value
    )
    if source and line and not looks_like_url:
        return f"{source}/{line}"
    if source:
        return source
    if line and looks_like_url:
        return fallback_host or "DecoTV"
    if line:
        return line
    return fallback_host or "DecoTV"


def split_name(folder):
    m = re.match(r'^(.*?)\s*\((\d{4})\)$', folder)
    return (m.group(1), m.group(2)) if m else (folder, "")


def read_url(path):
    try:
        return open(path, encoding="utf-8").read().strip()
    except Exception:
        return ""


def scan_library():
    items = []
    base = "/media/movies"
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            p = f"{base}/{d}"
            if not os.path.isdir(p):
                continue
            files = sorted(f for f in os.listdir(p) if f.endswith(".strm"))
            if files:
                items.append({"kind": "movie", "key": "movies/" + d, "folder": d,
                              "label": d, "season": 1, "dir": p, "files": files})
    base = "/media/tv"
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            sd = f"{base}/{d}"
            if not os.path.isdir(sd):
                continue
            for s in sorted(os.listdir(sd)):
                m = re.match(r"^Season (\d+)$", s)
                p = f"{sd}/{s}"
                if not m or not os.path.isdir(p):
                    continue
                files = sorted(f for f in os.listdir(p) if f.endswith(".strm"))
                if files:
                    n = int(m.group(1))
                    items.append({"kind": "tv", "key": f"tv/{d}/{s}", "folder": d,
                                  "label": f"{d} 第{n}季", "season": n,
                                  "dir": p, "files": files})
    return items


_COOKIE = None
_SEARCH_SOURCES = None


def get_cookie():
    global _COOKIE
    if _COOKIE is not None:
        return _COOKIE
    decotv = cfg.get("decotv", {})
    username = (decotv.get("admin_username") or "").strip()
    password = decotv.get("admin_password", "")
    if not username or not password:
        _COOKIE = cfg.get("cookie", "")
        log("未配置 DecoTV 管理员凭证，使用 config.json cookie")
        return _COOKIE

    login_url = (decotv.get("url") or "http://decotv:3000").rstrip("/") + "/api/login"
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
        _COOKIE = "; ".join(
            part.split(";", 1)[0].strip()
            for part in cookies
            if part.split(";", 1)[0].strip()
        )
    except (OSError, urllib.error.URLError) as error:
        log("DecoTV 登录失败:", type(error).__name__)
        _COOKIE = cfg.get("cookie", "")
    if not _COOKIE:
        log("DecoTV 登录未获得 Cookie")
    return _COOKIE


def candidates(name, year, season=1, kind=None):
    global _SEARCH_SOURCES
    cookie = get_cookie()
    if _SEARCH_SOURCES is None:
        _SEARCH_SOURCES = decotv_search.get_sources(cfg, cookie, http_json, log)

    out = []
    records = decotv_search.search_all(
        cfg, name, _SEARCH_SOURCES, cookie, http_json, log
    )
    for source, video in records:
        raw = video.get("vod_name", "")
        tags = raw + (video.get("type_name") or "") + (video.get("vod_remarks") or "")
        if any(word in tags for word in SKIP_WORDS):
            continue
        # 以本地条目的类型为默认值，只有候选带有相反的明确证据时才排除，
        # 防止把同名剧集的地址写进电影（或反过来）
        if kind and seasons.infer_item_kind(kind, raw, video) != kind:
            continue
        base, source_season, _ = seasons.parse_title(raw)
        if not (
            (base == name and source_season == season)
            or (season == 1 and seasons.clean(raw) == name)
        ):
            continue
        video_year = (video.get("vod_year") or "").strip()
        if (
            season == 1
            and year
            and video_year.isdigit()
            and abs(int(video_year) - int(year)) > 1
        ):
            continue
        for line_name, episodes in seasons.parse_play_lines(video):
            source_name = source.get("name") or urllib.parse.urlparse(
                source["api"]
            ).netloc
            out.append({"label": route_label(source_name, line_name, source_name), "eps": episodes})
    return out


def local_episode_numbers(it):
    """本地 STRM 文件名里的集号；任一文件无法解析则返回 None。"""
    numbers = []
    for name in it["files"]:
        match = re.search(r"S\d+E(\d+)", name, re.IGNORECASE)
        if not match:
            return None
        numbers.append(int(match.group(1)))
    return numbers


def covers_local(it, eps):
    """候选线路必须覆盖本地全部集号，否则切换后会混用不同来源或错位。"""
    if it["kind"] == "movie":
        return len(eps) >= 1
    local = local_episode_numbers(it)
    if local is None:
        return len(eps) >= len(it["files"])
    return set(local) <= {number for number, _ in seasons.number_episodes(eps)}


def write_back(it, name, eps):
    if it["kind"] == "movie":
        seasons.atomic_write(f"{it['dir']}/{it['files'][0]}", eps[0][1] + "\n")
    else:
        sn = it.get("season", 1)
        for number, url in seasons.number_episodes(eps):
            seasons.atomic_write(f"{it['dir']}/{name} S{sn:02d}E{number:02d}.strm", url + "\n")


def due(it, now):
    h = health.get(it["key"])
    if FORCE or not h:
        return True
    days = 1 if h["s"] in ("dead", "slow") else OK_DAYS
    return now - h["t"] >= days * 86400


def refresh_jellyfin():
    jf = cfg.get("jellyfin", {})
    if not jf.get("api_key"):
        return
    req = urllib.request.Request(
        jf["url"].rstrip("/") + "/Library/Refresh", method="POST",
        headers={"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'})
    try:
        urllib.request.urlopen(req, timeout=15)
        log("已通知 Jellyfin 扫描")
    except Exception as e:
        log("通知 Jellyfin 失败:", e)


def notify(text):
    if os.environ.get("STRM_SCHEDULED_NOTIFICATIONS") == "0":
        return
    tgc = cfg.get("telegram")
    if not tgc or not tgc.get("token"):
        return
    for chat in tgc.get("allowed_chat_ids", []):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{tgc['token']}/sendMessage",
                data=json.dumps({"chat_id": chat, "text": text[:4000]}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            log("Telegram 通知失败:", e)


def main():
    items = scan_library()
    if ONLY:
        items = [i for i in items if ONLY in i["folder"]]
    now = time.time()
    todo = [i for i in items if due(i, now)]
    todo.sort(key=lambda i: health.get(i["key"], {}).get("t", 0))
    todo = todo[:MAX_RUN]
    log(f"库中共 {len(items)} 部/季,本次检查 {len(todo)} 个"
        + (" [dry]" if DRY else "") + (" [optimize]" if OPT else ""))

    fixed, unfixed, ok_n = [], [], 0
    for it in todo:
        name, year = split_name(it["folder"])
        cur_url = read_url(f"{it['dir']}/{it['files'][0]}")
        cur = probe.score(cur_url) if cur_url else None
        hr = cur["headroom"] if cur else 0.0
        healthy = hr >= MIN_HR
        log(f"检查: {it['label']} | 当前: {fmt(cur)}")

        if healthy and (not OPT or hr >= 8.0):
            health[it["key"]] = {"t": now, "s": "ok"}
            ok_n += 1
            continue

        try:
            cands = candidates(name, year, it.get("season", 1), it["kind"])
        except ValueError as error:
            log("无法读取 DecoTV 搜索来源:", error)
            cands = []
        pool = [c for c in cands if covers_local(it, c["eps"]) and c["eps"][0][1] != cur_url]
        if pool:
            nmax = max(len(c["eps"]) for c in pool)
            pool = [c for c in pool if len(c["eps"]) == nmax]

        best, best_s, best_key = None, None, None
        for c in pool:
            s = probe.score(c["eps"][0][1])
            log(f"  候选 {c['label']}: {fmt(s)}")
            if not s:
                continue
            k = (min(s["headroom"], 8.0), -s["ttfb"])
            if best_key is None or k > best_key:
                best, best_s, best_key = c, s, k

        if best is None:
            switch = False
        elif cur is None:
            switch = True
        else:
            switch = best_s["headroom"] > hr and best_s["headroom"] >= min(hr * GAIN, 8.0)

        if switch:
            if DRY:
                log(f"  [dry] 将切换到 {best['label']}")
            else:
                write_back(it, name, best["eps"])
                log(f"  已切换到 {best['label']}")
            fixed.append(f"{it['label']} → {best['label']} ({best_s['mbps']:.1f}Mbps)")
            health[it["key"]] = {"t": now, "s": "fixed"}
        else:
            if not healthy:
                state = "dead" if cur is None else "slow"
                log("  没有更好的线路,保持不变")
                unfixed.append(f"{it['label']} ({'失效' if state == 'dead' else '偏慢'})")
                health[it["key"]] = {"t": now, "s": state}
            else:
                health[it["key"]] = {"t": now, "s": "ok"}
                ok_n += 1

    if not DRY:
        seasons.atomic_write(HEALTH, json.dumps(health, ensure_ascii=False))
        if fixed:
            refresh_jellyfin()

    log(f"完成:正常 {ok_n},已切换 {len(fixed)},无法修复 {len(unfixed)}")
    if (fixed or unfixed) and not DRY:
        msg = f"线路复测完成:正常 {ok_n},已切换 {len(fixed)},无法修复 {len(unfixed)}"
        if fixed:
            msg += "\n\n已切换:\n" + "\n".join(fixed)
        if unfixed:
            msg += "\n\n无法修复(没有更好的线路):\n" + "\n".join(unfixed)
        notify(msg)


if __name__ == "__main__":
    main()
