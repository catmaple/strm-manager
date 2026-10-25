#!/usr/bin/env python3
import sys, json, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import seasons, probe, decotv_search

SKIP_WORDS = ("预告", "花絮", "特辑")
UA = "Mozilla/5.0"


def http_json(url, cookie=""):
    headers = {"User-Agent": UA}
    if cookie:
        headers["Cookie"] = cookie
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as r:
        return json.load(r)


def log(*a):
    print(*a, flush=True)


def direct_cms_records(cfg, title):
    """未配置 DecoTV 管理员凭证时的回退：直接查询 config.json 的 cms_apis。"""
    records = []
    for api in cfg.get("cms_apis", []):
        url = f"{api}{'&' if '?' in api else '?'}ac=detail&wd={urllib.parse.quote(title)}"
        try:
            data = http_json(url)
        except Exception as e:
            print("搜索失败:", api, e)
            continue
        source = {"name": urllib.parse.urlparse(api).netloc, "api": api}
        records.extend((source, v) for v in data.get("list", []) if isinstance(v, dict))
    return records


def search_records(cfg, title):
    if not decotv_search.has_admin_credentials(cfg):
        log("未配置 DecoTV 管理员凭证，使用 config.json 的 cms_apis 搜索")
        return direct_cms_records(cfg, title)
    cookie = decotv_search.get_cookie(cfg, log, UA)
    sources = decotv_search.get_sources(cfg, cookie, http_json, log)
    return decotv_search.search_all(cfg, title, sources, cookie, http_json, log)


def main():
    cfg = json.load(open("/opt/m3u/config.json", encoding="utf-8"))
    if len(sys.argv) < 2:
        print('用法: add.py "片名 [第N季]"')
        sys.exit(1)

    title = sys.argv[1]
    tb, ts, _ = seasons.parse_title(title)
    full = seasons.clean(title)
    try:
        records = search_records(cfg, tb)
    except ValueError as error:
        print("无法读取 DecoTV 搜索来源:", error)
        sys.exit(1)

    groups = {}
    for source, v in records:
        raw = v.get("vod_name", "")
        tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
        if any(k in tags for k in SKIP_WORDS):
            continue
        b, sn, ex = seasons.parse_title(raw)
        if not ((b == tb and sn == ts) or seasons.clean(raw) == full):
            continue
        vy = (v.get("vod_year") or "").strip()
        vy = vy if vy.isdigit() else ""
        source_name = source.get("name") or urllib.parse.urlparse(source["api"]).netloc
        for ln, eps in seasons.parse_play_lines(v):
            c = groups.setdefault((b, sn, vy), {
                "name": b, "full": seasons.clean(raw), "season": sn,
                "explicit": ex, "year": vy,
                "kind": seasons.infer_item_kind("movie", raw, v), "lines": []})
            label = source_name if ln == source_name else f"{source_name}/{ln}"
            if all(line["label"] != label for line in c["lines"]):
                c["lines"].append({"label": label, "eps": eps})

    if not groups:
        print("没找到可用的 m3u8 资源")
        sys.exit(1)
    for c in groups.values():
        best = probe.pick(c["lines"])
        c["eps"] = best["eps"]
        folder, n = seasons.write_strm(c)
        print(f"已生成: {folder} ({n} 个) 线路 {best['label']}")


if __name__ == "__main__":
    main()
