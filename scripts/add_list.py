#!/usr/bin/env python3
import sys, json, urllib.parse

P = "/opt/m3u/config.json"
c = json.load(open(P, encoding="utf-8"))


def norm(u):
    s = urllib.parse.urlparse(u)
    decotv_url = c.get("decotv", {}).get("url", "http://decotv:3000")
    decotv = urllib.parse.urlparse(decotv_url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(s.query, keep_blank_values=True) if k != "start"]
    q.append(("start", "0"))
    return urllib.parse.urlunparse(s._replace(
        scheme=decotv.scheme or "http", netloc=decotv.netloc or "decotv:3000",
        query=urllib.parse.urlencode(q)))


cur, seen = [], set()
for u in c.get("list_urls", []) + [a for a in sys.argv[1:] if not a.startswith("--")]:
    n = norm(u)
    if n not in seen:
        seen.add(n)
        cur.append(n)
if "--show" not in sys.argv:
    c["list_urls"] = cur
    json.dump(c, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"当前共 {len(cur)} 个分类:")
for u in cur:
    print(" -", urllib.parse.unquote(u))
