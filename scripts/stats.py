#!/usr/bin/env python3
import os, re, sys, json, time, shutil, datetime, urllib.request

CONFIG = "/opt/m3u/config.json"
SNAP = "/opt/m3u/stats_snapshot.json"
CRAWL_STATE = "/opt/m3u/crawl_state.json"
HEALTH = "/opt/m3u/health.json"
MOVIES = "/media/movies"
TV = "/media/tv"
NO_SEND = "--no-send" in sys.argv     # 只打印,不发送,也不更新每周快照

cfg = json.load(open(CONFIG, encoding="utf-8"))


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def has_strm(p):
    try:
        return any(f.endswith(".strm") for f in os.listdir(p))
    except Exception:
        return False


def scan_library():
    movies, tv = [], {}
    if os.path.isdir(MOVIES):
        for d in sorted(os.listdir(MOVIES)):
            if has_strm(f"{MOVIES}/{d}"):
                movies.append(d)
    if os.path.isdir(TV):
        for d in sorted(os.listdir(TV)):
            sd = f"{TV}/{d}"
            if not os.path.isdir(sd):
                continue
            n_seasons, n_eps = 0, 0
            for s in os.listdir(sd):
                p = f"{sd}/{s}"
                if re.match(r"^Season \d+$", s) and os.path.isdir(p):
                    n = sum(1 for f in os.listdir(p) if f.endswith(".strm"))
                    if n:
                        n_seasons += 1
                        n_eps += n
            if n_seasons:
                tv[d] = {"seasons": n_seasons, "eps": n_eps}
    return movies, tv


def http_ok(url, want=None):
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            body = r.read(200).decode("utf-8", "ignore")
            return r.status < 500 and (want is None or want in body)
    except Exception:
        return False


def short(key):
    key = re.sub(r"^(movies|tv)/", "", key)
    return key.replace("/Season ", " 第").replace(" 第0", " 第") + ("季" if "/Season " in key or "Season" in key else "")


def build():
    now = datetime.datetime.now()
    movies, tv = scan_library()
    prev = load(SNAP, None)
    lines = [f"📊 影视库周报 {now:%Y-%m-%d}", ""]

    total_seasons = sum(v["seasons"] for v in tv.values())
    total_eps = sum(v["eps"] for v in tv.values())
    lines.append(f"库存:电影 {len(movies)} 部;剧集 {len(tv)} 部({total_seasons} 季,{total_eps} 集)")

    if prev is None:
        lines.append("本周新增:首次统计,已记录基线,下周起显示每周变化")
    else:
        new_movies = [m for m in movies if m not in set(prev.get("movies", []))]
        pt = prev.get("tv", {})
        new_series = [d for d in tv if d not in pt]
        grown = [(d, tv[d]["eps"] - pt[d]["eps"]) for d in tv
                 if d in pt and tv[d]["eps"] > pt[d]["eps"]]
        lines.append(f"本周新增:电影 {len(new_movies)} 部;新剧集 {len(new_series)} 部;"
                     f"有新集/新季的剧集 {len(grown)} 部")
        shown = ([f"  + {m}" for m in new_movies[:8]]
                 + [f"  + {d}" for d in new_series[:8]]
                 + [f"  ↑ {d} (+{n}集)" for d, n in grown[:5]])
        lines += shown
        rest = len(new_movies) + len(new_series) + len(grown) - len(shown)
        if rest > 0:
            lines.append(f"  ……另有 {rest} 项未列出")

    lines.append("")
    cc = cfg.get("crawl", {})
    total_types = sum(len(s.get("types", {})) for s in cc.get("sources", []))
    st = load(CRAWL_STATE, {})
    if st:
        done = sum(1 for v in st.values() if v.get("done"))
        ok = sum(1 for v in st.values() for x in v.get("seen", {}).values() if x > 0)
        lines.append(f"批量抓取:{done}/{total_types} 个分类已补完,累计入库 {ok} 部")
    else:
        lines.append("批量抓取:还没有运行记录")

    hl = load(HEALTH, {})
    if hl:
        cnt = {}
        for v in hl.values():
            cnt[v.get("s", "?")] = cnt.get(v.get("s", "?"), 0) + 1
        lines.append(f"线路复测:已检查 {len(hl)} 项,正常 {cnt.get('ok', 0)},"
                     f"已换线路 {cnt.get('fixed', 0)},偏慢 {cnt.get('slow', 0)},失效 {cnt.get('dead', 0)}")
        bad = [short(k) for k, v in hl.items() if v.get("s") in ("dead", "slow")]
        for b in bad[:10]:
            lines.append(f"  ! {b}")
        if len(bad) > 10:
            lines.append(f"  ……另有 {len(bad) - 10} 项")
    else:
        lines.append("线路复测:还没有运行记录")

    lines.append("")
    du = shutil.disk_usage("/")
    free_gb = du.free / 1024 ** 3
    warn = "  ⚠ 空间偏低,Jellyfin 要求至少 2GiB,建议清理或扩容" if free_gb < 3 else ""
    lines.append(f"磁盘:已用 {du.used / du.total * 100:.0f}%,剩余 {free_gb:.1f}GB{warn}")
    jf_ok = http_ok(cfg.get("jellyfin", {}).get("url", "http://127.0.0.1:8096").rstrip("/") + "/health")
    decotv_url = cfg.get("decotv", {}).get("url", "http://decotv:3000").rstrip("/")
    dt_ok = http_ok(decotv_url + "/")
    lines.append(f"服务:Jellyfin {'正常' if jf_ok else '❌ 无响应'};DecoTV {'正常' if dt_ok else '❌ 无响应'}")
    return "\n".join(lines), movies, tv


def send(text):
    tgc = cfg.get("telegram") or {}
    if not tgc.get("token"):
        print("未配置 Telegram,仅打印")
        return
    for chat in tgc.get("allowed_chat_ids", []):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{tgc['token']}/sendMessage",
                data=json.dumps({"chat_id": chat, "text": text[:4000]}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            print("Telegram 发送失败:", e)


def main():
    text, movies, tv = build()
    print(text)
    if NO_SEND:
        return
    if os.environ.get("STRM_SCHEDULED_NOTIFICATIONS") != "0":
        send(text)
    json.dump({"t": time.time(), "movies": movies, "tv": tv},
              open(SNAP, "w", encoding="utf-8"), ensure_ascii=False)


main()
