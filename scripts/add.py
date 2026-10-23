#!/usr/bin/env python3
import sys, json, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import seasons, probe

cfg = json.load(open("/opt/m3u/config.json", encoding="utf-8"))
if len(sys.argv) < 2:
    print('用法: add.py "片名 [第N季]"')
    sys.exit(1)

title = sys.argv[1]
tb, ts, _ = seasons.parse_title(title)
full = seasons.clean(title)
groups = {}
for api in cfg["cms_apis"]:
    url = f"{api}{'&' if '?' in api else '?'}ac=detail&wd={urllib.parse.quote(tb)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception as e:
        print("搜索失败:", api, e)
        continue
    host = urllib.parse.urlparse(api).netloc
    for v in data.get("list", []):
        raw = v.get("vod_name", "")
        tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
        if any(k in tags for k in ("预告", "花絮", "特辑")):
            continue
        b, sn, ex = seasons.parse_title(raw)
        if not ((b == tb and sn == ts) or seasons.clean(raw) == full):
            continue
        vy = (v.get("vod_year") or "").strip()
        vy = vy if vy.isdigit() else ""
        plays = v.get("vod_play_url", "").split("$$$")
        froms = v.get("vod_play_from", "").split("$$$")
        for i, g in enumerate(plays):
            if ".m3u8" not in g:
                continue
            eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
            eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u]
            if not eps:
                continue
            c = groups.setdefault((b, sn, vy), {
                "name": b, "full": seasons.clean(raw), "season": sn,
                "explicit": ex, "year": vy,
                "kind": seasons.infer_item_kind("movie", raw, v), "lines": []})
            ln = froms[i] if i < len(froms) else f"线路{i}"
            c["lines"].append({"label": f"{host}/{ln}", "eps": eps})

if not groups:
    print("没找到可用的 m3u8 资源")
    sys.exit(1)
for c in groups.values():
    best = probe.pick(c["lines"])
    c["eps"] = best["eps"]
    folder, n = seasons.write_strm(c)
    print(f"已生成: {folder} ({n} 个) 线路 {best['label']}")
