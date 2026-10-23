#!/usr/bin/env python3
import os, re, sys, json, time, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import probe
import seasons

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


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def fmt(s):
    if not s:
        return "失败/不可用"
    return f"{s['mbps']:.1f}Mbps/需{s['need']:.1f} (余量{s['headroom']:.1f}x, 首包{s['ttfb']:.2f}s)"


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


def candidates(name, year, season=1):
    out = []
    for api in cfg["cms_apis"]:
        url = f"{api}{'&' if '?' in api else '?'}ac=detail&wd={urllib.parse.quote(name)}"
        try:
            data = http_json(url)
        except Exception as e:
            log("  搜索失败:", api, e)
            continue
        host = urllib.parse.urlparse(api).netloc
        for v in data.get("list", []):
            raw = v.get("vod_name", "")
            tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
            if any(k in tags for k in SKIP_WORDS):
                continue
            b, sn, _ = seasons.parse_title(raw)
            if not ((b == name and sn == season) or (season == 1 and seasons.clean(raw) == name)):
                continue
            vy = (v.get("vod_year") or "").strip()
            if season == 1 and year and vy.isdigit() and abs(int(vy) - int(year)) > 1:
                continue
            groups = v.get("vod_play_url", "").split("$$$")
            froms = v.get("vod_play_from", "").split("$$$")
            for i, g in enumerate(groups):
                if ".m3u8" not in g:
                    continue
                eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
                eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u]
                if eps:
                    ln = froms[i] if i < len(froms) else f"线路{i}"
                    out.append({"label": f"{host}/{ln}", "eps": eps})
        time.sleep(1)
    return out


def write_back(it, name, eps):
    if it["kind"] == "movie":
        open(f"{it['dir']}/{it['files'][0]}", "w", encoding="utf-8").write(eps[0][1] + "\n")
    else:
        sn = it.get("season", 1)
        for i, (_, u) in enumerate(eps, 1):
            open(f"{it['dir']}/{name} S{sn:02d}E{i:02d}.strm", "w", encoding="utf-8").write(u + "\n")


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

        cands = candidates(name, year, it.get("season", 1))
        need = len(it["files"])
        pool = [c for c in cands if len(c["eps"]) >= need and c["eps"][0][1] != cur_url]
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
        json.dump(health, open(HEALTH, "w", encoding="utf-8"), ensure_ascii=False)
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


main()
