#!/usr/bin/env python3
import os, re, sys, json, time, datetime, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import decotv_search
import probe
import seasons

CONFIG = "/opt/m3u/config.json"
STATE = "/opt/m3u/crawl_state.json"
UA = "Mozilla/5.0"
DRY = "--dry" in sys.argv
SKIP_WORDS = ("预告", "花絮", "特辑")

cfg = json.load(open(CONFIG, encoding="utf-8"))
cc = cfg.get("crawl", {})
state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
counters = {"added": 0, "pages": 0}
_SEARCH_SOURCES = None
_PLAYBACK_CACHE = {}


def http_json(url, cookie=""):
    headers = {"User-Agent": UA}
    if cookie:
        headers["Cookie"] = cookie
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as r:
        return json.load(r)


def arg(name, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        try:
            return int(sys.argv[i + 1])
        except Exception:
            pass
    return default


MAX_NEW = arg("--new", cc.get("max_new_per_run", 150))
DRY_MAX_NEW = 3
DRY_DEADLINE = 90
START_TIME = time.time()
if DRY:
    MAX_NEW = min(MAX_NEW, DRY_MAX_NEW)
MAX_PAGES = arg("--pages", cc.get("max_pages_per_run", 80))
TYPE_CAP = arg("--per-type", cc.get("per_type_new", 15))
TYPE_PAGES = cc.get("per_type_pages", 10)
MIN_YEAR = cc.get("min_year", 0)
if cc.get("max_age_years"):
    MIN_YEAR = datetime.date.today().year - int(cc["max_age_years"])
SKIP_REMARKS = [k.upper() for k in cc.get("skip_remarks", ["TC", "TS", "CAM", "枪版", "抢先", "预告"])]
MIN_SCORE = float(cc.get("min_score", 0))          # 0 = 不按评分过滤
SCORE_FIELDS = {"douban": ["vod_douban_score"], "vod": ["vod_score"],
                "any": ["vod_douban_score", "vod_score"]}.get(cc.get("score_field", "douban"), ["vod_douban_score"])


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def out_of_time():
    return DRY and time.time() - START_TIME > DRY_DEADLINE


_COOKIE = None


def get_cookie():
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
    req = urllib.request.Request(login_url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            _COOKIE = "; ".join(c.split(";", 1)[0].strip() for c in (r.headers.get_all("Set-Cookie") or []))
    except Exception as e:
        log("登录失败:", e)
        _COOKIE = cfg.get("cookie", "")
    return _COOKIE


def fetch(api, qs):
    target = api.rstrip("/") + "/?" + urllib.parse.urlencode(qs)
    headers = {"User-Agent": UA}
    decotv_url = cfg.get("decotv", {}).get("url", "http://decotv:3000").rstrip("/")
    url = decotv_url + "/api/proxy/cms?url=" + urllib.parse.quote(target, safe="")
    ck = get_cookie()
    if ck:
        headers["Cookie"] = ck
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            log("  请求失败:", str(e)[:100], f"(第{i + 1}次)")
            time.sleep(2 * (i + 1))
    return None


def find_playback(raw, year, configured_kind):
    global _SEARCH_SOURCES
    base, season, _ = seasons.parse_title(raw)
    if not base:
        return None
    year = str(year or "").strip()
    cache_key = (base.casefold(), season, year, configured_kind)
    if cache_key in _PLAYBACK_CACHE:
        return _PLAYBACK_CACHE[cache_key]
    cookie = get_cookie()
    if _SEARCH_SOURCES is None:
        _SEARCH_SOURCES = decotv_search.get_sources(cfg, cookie, http_json, log)
    search_started = time.time()
    records = decotv_search.search_all(
        cfg, base, _SEARCH_SOURCES, cookie, http_json, log
    )
    log(f"  聚合搜索耗时 {time.time() - search_started:.1f}s: {base}")
    candidates = {}
    for source, item in records:
        name = item.get("vod_name", "")
        tags = name + (item.get("type_name") or "") + (item.get("vod_remarks") or "")
        if any(word in tags for word in SKIP_WORDS):
            continue
        item_base, item_season, explicit = seasons.parse_title(name)
        if item_base.casefold() != base.casefold() or item_season != season:
            continue
        item_year = str(item.get("vod_year") or "").strip()
        item_year = item_year if item_year.isdigit() else ""
        if year and item_year and abs(int(item_year) - int(year)) > 1:
            continue
        for line_name, episodes in seasons.parse_play_lines(item):
            line_label = f"{source['name']}/{line_name}"
            key = (item_base, item_season, item_year, line_label)
            candidate = candidates.setdefault(key, {
                "name": item_base,
                "full": seasons.clean(name),
                "season": item_season,
                "explicit": explicit,
                "year": item_year or year,
                "kind": seasons.infer_item_kind(configured_kind, name, item),
                "label": line_label,
                "eps": [],
            })
            existing_urls = {url for _, url in candidate["eps"]}
            candidate["eps"].extend(
                (episode_name, url)
                for episode_name, url in episodes
                if url not in existing_urls
            )
    choices = list(candidates.values())
    selected = probe.pick(choices, log) if choices else None
    _PLAYBACK_CACHE[cache_key] = selected
    if selected:
        log("  聚合搜索选用线路:", selected["label"], len(selected["eps"]), "集")
    else:
        log("  DecoTV 聚合搜索未找到可用播放源:", raw)
    return selected


def get_score(v):
    """返回评分;没有评分(空、0、无法解析)返回 0.0。"""
    for f in SCORE_FIELDS:
        try:
            x = float(str(v.get(f) or "0").strip() or 0)
        except ValueError:
            x = 0.0
        if x > 0:
            return x
    return 0.0


def skip_reason(v, raw):
    remarks = v.get("vod_remarks") or ""
    tags = raw + (v.get("type_name") or "") + remarks
    if any(k in tags for k in SKIP_WORDS):
        return "预告/花絮"
    if any(k in remarks.upper() for k in SKIP_REMARKS):
        return "低清/抢先"
    y = (v.get("vod_year") or "").strip()
    if MIN_YEAR and y.isdigit() and int(y) < MIN_YEAR:
        return "年份过早"
    if cc.get("skip_unknown_year") and not y.isdigit():
        return "无年份"
    if MIN_SCORE:
        sc = get_score(v)
        if 0 < sc < MIN_SCORE:      # 有评分且低于门槛才跳过;没有评分的放行
            return f"评分过低({sc})"
    return None


def exists_on_disk(r):
    if seasons.is_tv(r):
        d = seasons.find_series_dir(r["name"], r["year"], r["season"])
        return os.path.isdir(f"{seasons.TV_ROOT}/{d}/Season {r['season']:02d}")
    folder = f"{r['full']} ({r['year']})" if r["year"] else r["full"]
    return os.path.isdir(f"{seasons.MOVIE_ROOT}/{folder}")


def process_item(st, kind, v):
    """返回 (状态, 是否首次见到)。状态:added/updated/known/skipped"""
    vid = str(v.get("vod_id"))
    prev = st["seen"].get(vid)
    raw = v.get("vod_name", "")
    fresh = prev is None
    reason = skip_reason(v, raw)
    if reason:
        st["seen"][vid] = -1
        return "skipped", fresh
    metadata_kind = seasons.infer_item_kind(kind, raw, v)
    if prev is not None and prev > 0 and metadata_kind != "tv":
        return "known", False
    base, season, explicit = seasons.parse_title(raw)
    if not base:
        st["seen"][vid] = -1
        return "skipped", fresh
    year = (v.get("vod_year") or "").strip()
    year = year if year.isdigit() else ""
    playback = find_playback(raw, year, metadata_kind)
    if not playback:
        st["seen"][vid] = 0
        return "skipped", fresh
    eps = playback["eps"]
    n = len(eps)
    mx = cc.get("max_eps", 0)
    if mx and n > mx and not (prev and prev > 0):
        st["seen"][vid] = -1
        return "skipped", fresh
    item_kind = playback["kind"]
    if prev is not None and prev > 0 and not (item_kind == "tv" and n > prev):
        return "known", False
    r = {"name": playback["name"], "full": playback["full"], "season": playback["season"],
         "explicit": playback["explicit"] or item_kind == "tv", "year": playback["year"],
         "eps": eps, "kind": playback["kind"]}
    key = f"{base}|{year}|{season}"
    owner = st["claimed"].get(key)
    if (owner and owner != vid) or (not owner and exists_on_disk(r)):
        st["seen"][vid] = -1  # 同名同年份已有,跳过,不覆盖
        return "skipped", fresh
    if DRY:
        log(f"  [dry] 将加入: {r['full']} ({year}) {'剧集' if seasons.is_tv(r) else '电影'} {n}个")
    else:
        folder, cnt = seasons.write_strm(r)
        log(f"  + {folder} ({cnt}个)")
    st["claimed"][key] = vid
    st["seen"][vid] = n
    return ("updated" if (prev or 0) > 0 else "added"), fresh


def save_state():
    if DRY:
        return
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def scan(src, tid, kind, st):
    update_mode = st["done"]
    page = 1 if update_mode else st["next"]
    walked = 0
    start_added = counters["added"]
    while True:
        if counters["added"] >= MAX_NEW or counters["pages"] >= MAX_PAGES:
            break
        if walked >= (cc.get("update_pages", 15) if update_mode else TYPE_PAGES):
            break
        data = fetch(src["api"], {"ac": "videolist", "pg": page, "t": tid})
        counters["pages"] += 1
        walked += 1
        if data is None:
            break
        items = data.get("list") or []
        if not items:
            if not update_mode:
                st["done"] = True
            break
        changed, partial = False, False
        for v in items:
            if counters["added"] >= MAX_NEW or counters["added"] - start_added >= TYPE_CAP:
                partial = True
                break
            if out_of_time():
                log(f"Dry Run 已达 {DRY_DEADLINE} 秒预算,提前结束")
                return
            status, fresh = process_item(st, kind, v)
            if status in ("added", "updated"):
                counters["added"] += 1
                changed = True
            elif fresh:
                changed = True
        if partial:
            if not update_mode:
                st["next"] = page      # 下次从同一页继续,已入库的会被跳过
            break
        if not update_mode:
            st["next"] = page + 1
            bmax = cc.get("backfill_max_pages", 0)
            if page >= int(data.get("pagecount") or 0) or (bmax and page >= bmax):
                st["done"] = True
                break
        elif not changed:
            break
        page += 1
        if counters["pages"] % 10 == 0:
            save_state()
        time.sleep(cc.get("delay", 1.0))


def list_types():
    for src in cc.get("sources", []):
        data = fetch(src["api"], {"ac": "list"})
        print(f"\n{src['name']}  {src['api']}")
        if not data:
            print("  获取分类失败")
            continue
        for c in data.get("class", []):
            d = fetch(src["api"], {"ac": "videolist", "pg": 1, "t": c["type_id"]})
            total = d.get("total") if d else "?"
            print(f"  t={c['type_id']:<4} {c['type_name']:<10} (父类 {c.get('type_pid')})  共 {total} 部")
            time.sleep(1)


def show_status():
    for k, st in state.items():
        n_ok = sum(1 for x in st["seen"].values() if x > 0)
        print(f"{k}: {'已补完' if st['done'] else '补库中,下次从第 %s 页' % st['next']},"
              f" 已记录 {len(st['seen'])} 条,已入库 {n_ok} 部")


def refresh_jellyfin():
    jf = cfg.get("jellyfin", {})
    if not jf.get("api_key"):
        return
    req = urllib.request.Request(jf["url"].rstrip("/") + "/Library/Refresh", method="POST",
                                 headers={"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'})
    try:
        urllib.request.urlopen(req, timeout=15)
        log("已通知 Jellyfin 扫描")
    except Exception as e:
        log("通知 Jellyfin 失败:", e)


def notify(text):
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
    if "--types" in sys.argv:
        return list_types()
    if "--status" in sys.argv:
        return show_status()
    tasks = []
    for src in cc.get("sources", []):
        for tid, kind in src["types"].items():
            tasks.append((src, tid, kind, f"{src['name']}|{tid}"))
    if "--type" in sys.argv:
        _t = sys.argv[sys.argv.index("--type") + 1]
        tasks = [t for t in tasks if t[1] == _t]
    if not tasks:
        log("未配置 crawl.sources(或 --type 没有匹配的分类)")
        return
    off = int(time.time() // 86400) % len(tasks)       # 每天轮换起始分类
    tasks = tasks[off:] + tasks[:off]
    tasks.sort(key=lambda t: not state.get(t[3], {}).get("done", False))  # 已补完的先跑增量
    log(f"开始:只收录 {MIN_YEAR} 年及以后的作品,本次最多新增 {MAX_NEW} 部,最多翻 {MAX_PAGES} 页"
        + (" [dry]" if DRY else ""))
    for src, tid, kind, key in tasks:
        if counters["added"] >= MAX_NEW or counters["pages"] >= MAX_PAGES or out_of_time():
            break
        st = state.setdefault(key, {"next": 1, "done": False, "seen": {}, "claimed": {}})
        log(f"处理 {key} ({kind}):" + ("增量更新" if st["done"] else f"补库,从第 {st['next']} 页"))
        scan(src, tid, kind, st)
    save_state()
    log(f"完成:新增/更新 {counters['added']} 部,翻页 {counters['pages']} 次")
    if counters["added"] and not DRY:
        refresh_jellyfin()
        notify(f"批量抓取完成:新增/更新 {counters['added']} 部")


main()
