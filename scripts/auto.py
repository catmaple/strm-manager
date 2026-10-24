#!/usr/bin/env python3
import os, re, sys, json, time, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import probe
import seasons
import decotv_search

CONFIG = "/opt/m3u/config.json"
STATE = "/opt/m3u/state.json"
DRY = "--dry" in sys.argv
UA = "Mozilla/5.0"
SKIP_WORDS = ("预告", "花絮", "特辑")

cfg = json.load(open(CONFIG, encoding="utf-8"))
state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def http_json(url, cookie=""):
    h = {"User-Agent": UA}
    if cookie:
        h["Cookie"] = cookie
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25) as r:
        return json.load(r)


def find_items(o):
    if isinstance(o, list) and o and isinstance(o[0], dict) and any(k in o[0] for k in ("title", "name")):
        return o
    if isinstance(o, dict):
        for v in o.values():
            r = find_items(v)
            if r:
                return r
    return []


_COOKIE = None


def get_cookie():
    """每次运行使用 DecoTV 管理员凭证登录一次拿新 Cookie。"""
    global _COOKIE
    if _COOKIE is not None:
        return _COOKIE
    decotv = cfg.get("decotv", {})
    username = decotv.get("admin_username", "")
    password = decotv.get("admin_password", "")
    if not username or not password:
        _COOKIE = cfg.get("cookie", "")
        log("警告:未配置 DecoTV 管理员凭证，使用 config.json cookie")
        return _COOKIE
    login_url = decotv.get("url", "http://decotv:3000").rstrip("/") + "/api/login"
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        login_url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            parts = [c.split(";", 1)[0].strip() for c in (r.headers.get_all("Set-Cookie") or [])]
            _COOKIE = "; ".join(parts)
    except Exception as e:
        log("登录失败:", e)
        _COOKIE = cfg.get("cookie", "")
    log("登录成功,已获取 Cookie" if _COOKIE else "警告:未获得 Cookie")
    return _COOKIE


def with_start(url, start):
    p = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    found = False
    out = []
    for k, v in q:
        if k == "start":
            out.append((k, str(start)))
            found = True
        else:
            out.append((k, v))
    if not found:
        out.append(("start", str(start)))
    return urllib.parse.urlunparse(p._replace(query=urllib.parse.urlencode(out)))


def get_titles():
    seen, out = set(), []
    max_pages = cfg.get("max_pages", 20)
    for u in cfg["list_urls"]:
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(u).query))
        try:
            limit = int(qs.get("limit", 25))
        except ValueError:
            limit = 25
        total, pages = 0, 0
        for page in range(max_pages):
            pu = with_start(u, page * limit)
            try:
                data = http_json(pu, get_cookie())
            except Exception as e:
                log("取榜单失败:", pu, e)
                break
            items = find_items(data)
            if not items:
                break
            pages += 1
            total += len(items)
            for it in items:
                t = seasons.clean(it.get("title") or it.get("name") or "")
                m = re.search(r"(19|20)\d{2}", str(it.get("year") or ""))
                y = m.group(0) if m else ""
                if t and (t, y) not in seen:
                    seen.add((t, y))
                    out.append((t, y))
            time.sleep(0.5)
            if len(items) < limit:
                break
        log(f"榜单 {pages} 页 / {total} 条:", u[:110])
    return out


def search(title, year, sources, cookie):
    base, season, _ = seasons.parse_title(title)
    full = seasons.clean(title)
    grouped = {}
    for source, v in decotv_search.search_all(cfg, base, sources, cookie, http_json, log):
        raw = v.get("vod_name", "")
        tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
        if any(k in tags for k in SKIP_WORDS):
            continue
        b, sn, explicit = seasons.parse_title(raw)
        if not ((b == base and sn == season) or seasons.clean(raw) == full):
            continue
        vy = (v.get("vod_year") or "").strip()
        if year and vy.isdigit() and abs(int(vy) - int(year)) > 1:
            continue
        groups = v.get("vod_play_url", "").split("$$$")
        froms = v.get("vod_play_from", "").split("$$$")
        record_lines = []
        for i, g in enumerate(groups):
            eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
            eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u.lower()]
            if eps:
                ln = froms[i] if i < len(froms) else f"线路{i}"
                record_lines.append((f"{source['name']}/{ln}", eps))
        if not record_lines:
            continue
        item_year = vy if vy.isdigit() else year
        candidate = grouped.setdefault((b, sn, item_year), {
            "name": b,
            "full": seasons.clean(raw),
            "season": sn,
            "explicit": explicit,
            "year": item_year,
            "kind": seasons.infer_item_kind("movie", raw, v),
            "lines": {},
        })
        for line_label, eps in record_lines:
            line = candidate["lines"].setdefault(
                line_label, {"label": line_label, "eps": []}
            )
            existing_urls = {url for _, url in line["eps"]}
            line["eps"].extend(
                (episode_name, url)
                for episode_name, url in eps
                if url not in existing_urls
            )
    return [
        {**{key: value for key, value in candidate.items() if key != "lines"}, **line}
        for candidate in grouped.values()
        for line in candidate["lines"].values()
    ]


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


def main():
    titles = get_titles()
    log(f"共发现 {len(titles)} 部不重复的片")
    if "--titles" in sys.argv:
        return
    if not titles:
        log("没有取到任何片名,请检查 list_urls 和登录/cookie 配置")
        return
    cookie = get_cookie()
    try:
        sources = decotv_search.get_sources(cfg, cookie, http_json, log)
    except ValueError as error:
        log("无法初始化 DecoTV 搜索:", error)
        return
    if not sources:
        log("没有配置可搜索的 DecoTV 来源")
        return
    added, now, lookups = 0, time.time(), 0
    retry = cfg.get("retry_miss_days", 3) * 86400
    lookup_version = "decotv-all-sources-v1"
    for title, year in titles:
        if added >= cfg.get("max_new_per_run", 30):
            log("已达到本次新增上限")
            break
        key = f"{title}|{year}"
        st = state.get(key)
        if st and st["s"] == "ok":
            continue
        if (
            st
            and st["s"] == "miss"
            and st.get("lookup_version") == lookup_version
            and now - st["t"] < retry
        ):
            continue
        if lookups >= cfg.get("max_lookups_per_run", 300):
            log("已达到本次查找上限,剩余的留到下次")
            break
        lookups += 1
        if lookups % 20 == 0 and not DRY:
            json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        log("查找:", title, year)
        cands = search(title, year, sources, cookie)
        hit = probe.pick(cands, log) if cands else None
        if hit:
            log("  选用线路:", hit.get("label"), "|", hit.get("_info"))
        if not hit:
            log("  未找到")
            state[key] = {"s": "miss", "t": now, "lookup_version": lookup_version}
            continue
        if DRY:
            log(f"  [dry] 将生成: {hit['full']} ({hit['year']}) {len(hit['eps'])} 个")
            continue
        folder, n = seasons.write_strm(hit)
        log(f"  已生成: {folder} ({n} 个)")
        state[key] = {"s": "ok", "t": now}
        added += 1
    if not DRY:
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        if added:
            refresh_jellyfin()
    log(f"完成,本次新增 {added} 部")


main()
