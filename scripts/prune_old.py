#!/usr/bin/env python3
import os, re, sys, shutil, datetime

APPLY = "--apply" in sys.argv
limit = datetime.date.today().year - 20
print(f"清理 {limit} 年以前的作品" + ("" if APPLY else "(预览,加 --apply 才会删除)"))
n = 0
for root, tv in (("/media/movies", False), ("/media/tv", True)):
    if not os.path.isdir(root):
        continue
    for d in sorted(os.listdir(root)):
        m = re.match(r"^(.*?)\s*\((\d{4})\)$", d)
        if not m or int(m.group(2)) >= limit:
            continue
        path = f"{root}/{d}"
        if tv:
            seasons = [x for x in os.listdir(path) if x.startswith("Season ")]
            if len(seasons) > 1:
                print(f"保留(多季,可能含新季): {d} {sorted(seasons)}")
                continue
        print(f"{'删除' if APPLY else '[预览]'}: {path}")
        n += 1
        if APPLY:
            shutil.rmtree(path)
print(f"共 {n} 个目录" + ("已删除" if APPLY else "待删除"))
