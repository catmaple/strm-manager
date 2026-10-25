#!/usr/bin/env python3
import re, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0"
TIMEOUT = 8
SAMPLE = 2


def _open(url):
    # 播放地址来自第三方 CMS，只允许 http(s)，避免 file:// 等协议被利用
    if urllib.parse.urlsplit(url).scheme.lower() not in ("http", "https"):
        raise ValueError("unsupported URL scheme")
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT)


def _get_text(url):
    t0 = time.time()
    with _open(url) as r:
        ttfb = time.time() - t0
        data = r.read(2 * 1024 * 1024)
    return data.decode("utf-8", "ignore"), ttfb


def _download(url, limit=16 * 1024 * 1024, deadline=12.0):
    t0, n, complete = time.time(), 0, True
    with _open(url) as r:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            n += len(chunk)
            if n >= limit or time.time() - t0 > deadline:
                complete = False
                break
    return n, time.time() - t0, complete


def _parse_master(text, base):
    best, best_bw, best_resolution = None, -1, ""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", ln)
            bw = int(m.group(1)) if m else 0
            resolution_match = re.search(r"RESOLUTION=(\d+x\d+)", ln, re.IGNORECASE)
            resolution = resolution_match.group(1) if resolution_match else ""
            for nxt in lines[i + 1:]:
                if nxt.strip() and not nxt.startswith("#"):
                    if bw > best_bw:
                        best = urllib.parse.urljoin(base, nxt.strip())
                        best_bw = bw
                        best_resolution = resolution
                    break
    return best, best_resolution


def _parse_media(text, base):
    segs, dur = [], 0.0
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("#EXTINF:"):
            try:
                dur = float(ln[8:].split(",")[0])
            except ValueError:
                dur = 0.0
        elif ln and not ln.startswith("#"):
            segs.append((dur, urllib.parse.urljoin(base, ln)))
            dur = 0.0
    return segs


def score(url):
    """返回 {ttfb, mbps, need, headroom};失败返回 None。"""
    try:
        text, ttfb = _get_text(url)
        resolution = ""
        if "#EXT-X-STREAM-INF" in text:
            sub, resolution = _parse_master(text, url)
            if not sub:
                return None
            url = sub
            text, t2 = _get_text(url)
            ttfb += t2
        segs = _parse_media(text, url)
        if not segs:
            return None
        mid = len(segs) // 2
        sample = segs[mid:mid + SAMPLE] or segs[-SAMPLE:]
        tb, tt, td = 0, 0.0, 0.0
        for dur, su in sample:
            n, t, complete = _download(su)
            if not complete:
                return {"ttfb": ttfb, "mbps": n * 8 / max(t, 0.01) / 1e6,
                        "need": 1.0, "headroom": 0.1, "resolution": resolution}
            tb, tt, td = tb + n, tt + t, td + dur
        td = td or 6.0 * len(sample)
        if tb == 0 or tt <= 0:
            return None
        mbps = tb * 8 / tt / 1e6
        need = tb * 8 / td / 1e6
        return {
            "ttfb": ttfb,
            "mbps": mbps,
            "need": need,
            "headroom": mbps / need,
            "resolution": resolution,
        }
    except Exception:
        return None


def pick(cands, log=print):
    """cands: [{'label':..., 'eps':[(名,url),...]}];返回最快的一个,并写入 _info。"""
    if not cands:
        return None
    nmax = max(len(c["eps"]) for c in cands)
    pool = [c for c in cands if len(c["eps"]) == nmax]
    if len(pool) == 1:
        pool[0]["_info"] = "仅一条线路,未测速"
        return pool[0]
    best, best_key = None, None
    for c in pool:
        s = score(c["eps"][0][1])
        name = c.get("label", "?")
        if s is None:
            c["_info"] = "测速失败"
            log(f"  线路 {name}: 不可用/超时")
            continue
        resolution = f"{s['resolution']} / " if s.get("resolution") else ""
        c["_info"] = (
            f"{resolution}{s['mbps']:.1f}Mbps/需{s['need']:.1f}, "
            f"{s['ttfb']:.2f}s"
        )
        log(f"  线路 {name}: {c['_info']}")
        key = (min(s["headroom"], 8.0), -s["ttfb"])
        if best_key is None or key > best_key:
            best, best_key = c, key
    if best is None:
        pool[0]["_info"] = "全部测速失败,使用第一条"
        return pool[0]
    return best


def rank(cands, log=print, workers=6):
    """测速并按可用余量排序所有线路，不按集数筛选；线路并发测速。"""
    cands = list(cands)

    def measure(candidate):
        return score(candidate["eps"][0][1]) if candidate.get("eps") else None

    if cands:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(cands)))) as pool:
            measured = list(pool.map(measure, cands))
    else:
        measured = []
    ranked = []
    for candidate, info in zip(cands, measured):
        if info is None:
            candidate["_info"] = "测速失败 / 分辨率未知"
            log(f"  线路 {candidate.get('label', '?')}: 不可用/超时")
            continue
        resolution = info.get("resolution")
        if not resolution:
            quality = re.search(
                r"(?<!\d)(2160|1440|1080|720|576|480|360)p\b"
                r"|(?<!\d)(\d{3,4}x\d{3,4})(?!\d)",
                f"{candidate.get('label', '')} {candidate['eps'][0][1]}",
                re.IGNORECASE,
            )
            resolution = (
                f"{quality.group(1)}p" if quality and quality.group(1)
                else quality.group(2) if quality
                else ""
            )
        resolution = resolution or "分辨率未知"
        candidate["_info"] = (
            f"{resolution} / {info['mbps']:.1f}Mbps/需{info['need']:.1f}, "
            f"{info['ttfb']:.2f}s"
        )
        candidate["_rank"] = (min(info["headroom"], 8.0), -info["ttfb"])
        ranked.append(candidate)
        log(f"  线路 {candidate.get('label', '?')}: {candidate['_info']}")
    ranked.sort(key=lambda item: item["_rank"], reverse=True)
    for candidate in ranked:
        candidate.pop("_rank", None)
    return ranked
