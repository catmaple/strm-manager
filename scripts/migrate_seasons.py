#!/usr/bin/env python3
import os, re, sys
sys.path.insert(0, "/opt/m3u")
import seasons

APPLY = "--apply" in sys.argv
ROOT = seasons.TV_ROOT
moved = 0
for d in sorted(os.listdir(ROOT)):
    src_root = f"{ROOT}/{d}"
    if not os.path.isdir(src_root):
        continue
    fbase, fyear = seasons.split_folder(d)
    base, season, explicit = seasons.parse_title(fbase)
    if not base or not (explicit or season > 1):
        continue
    target = seasons.find_series_dir(base, fyear, season)
    if target == d:
        continue
    for sd in sorted(os.listdir(src_root)):
        sp = f"{src_root}/{sd}"
        if not os.path.isdir(sp):
            continue
        files = sorted(f for f in os.listdir(sp) if f.endswith(".strm"))
        if not files:
            continue
        dst = f"{ROOT}/{target}/Season {season:02d}"
        if os.path.isdir(dst) and any(f.endswith(".strm") for f in os.listdir(dst)):
            print(f"跳过(目标已有文件): {d}/{sd} -> {target}/Season {season:02d}")
            continue
        print(f"{'移动' if APPLY else '[预览]'}: {d}/{sd} ({len(files)}集) -> {target}/Season {season:02d}")
        moved += 1
        if not APPLY:
            continue
        os.makedirs(dst, exist_ok=True)
        for i, f in enumerate(files, 1):
            m = re.search(r"S\d+E(\d+)", f)
            ep = int(m.group(1)) if m else i
            url = open(f"{sp}/{f}", encoding="utf-8").read()
            open(f"{dst}/{base} S{season:02d}E{ep:02d}.strm", "w", encoding="utf-8").write(url)
            os.remove(f"{sp}/{f}")
        try:
            os.rmdir(sp)
        except OSError:
            pass
    if APPLY:
        try:
            os.rmdir(src_root)
        except OSError:
            pass
print(f"共 {moved} 个季目录" + ("已迁移" if APPLY else "待迁移(加 --apply 执行)"))
