#!/usr/bin/env python3
import os, re, tempfile

TV_ROOT = "/media/tv"
MOVIE_ROOT = "/media/movies"

_CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
       "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})"
# (正则, 是否明确表示"季")。"第N部"只作季号参考,不单独当作剧集的证据
_PATS = [
    (re.compile(r"第\s*" + _NUM + r"\s*季"), True),
    (re.compile(r"第\s*" + _NUM + r"\s*部"), False),
    (re.compile(r"[Ss]eason\s*([0-9]{1,2})"), True),
    (re.compile(r"(?:^|\s)[Ss]([0-9]{1,2})$"), True),
]
_TAIL = r"[\s:：\-—–·、,，]+$"


def cn2int(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        tens = (_CN.get(a) if a else 1)
        ones = (_CN.get(b) if b else 0)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return _CN.get(s)


def clean(s):
    s = re.sub(r"[\[\(（【].*?[\]\)）】]", "", s or "")
    s = re.sub(r'[\\/:*?"<>|]', " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_title(raw):
    """返回 (剧名, 季号, 是否明确标了季)。季号默认 1。"""
    s = raw or ""
    season, explicit = 1, False
    for pat, exp in _PATS:
        m = pat.search(s)
        if m:
            n = cn2int(m.group(1))
            if n and 0 < n < 100:
                season, explicit = n, exp
                s = s[:m.start()] + " " + s[m.end():]
                break
    base = re.sub(_TAIL, "", clean(s)).strip()
    return base, season, explicit


def split_folder(folder):
    m = re.match(r"^(.*?)\s*\((\d{4})\)$", folder)
    return (m.group(1), m.group(2)) if m else (folder, "")


def find_series_dir(base, year, season):
    """已有同名剧集目录就复用;后面的季可以和第一季年份不同。"""
    if os.path.isdir(TV_ROOT):
        for d in sorted(os.listdir(TV_ROOT)):
            b, y = split_folder(d)
            if b != base:
                continue
            if season > 1 or not year or not y or abs(int(y) - int(year)) <= 1:
                return d
    return f"{base} ({year})" if year else base


def is_tv(r):
    if r.get("kind") in {"movie", "tv"}:
        return r["kind"] == "tv"
    return len(r["eps"]) > 1 or bool(r.get("explicit"))


_MOVIE_CATEGORY_TOKENS = (
    "电影", "影片", "剧场版", "大电影", "微电影", "动作片", "喜剧片", "爱情片",
    "科幻片", "恐怖片", "战争片", "犯罪片", "剧情片", "悬疑片", "惊悚片",
    "奇幻片", "冒险片", "灾难片", "movie", "film",
)
_TV_CATEGORY_TOKENS = (
    "电视剧", "连续剧", "剧集", "短剧", "网剧", "番剧", "动漫", "综艺",
    "series", "anime",
)
# 韩剧/国产剧/欧美剧等；排除喜剧、悲剧、剧情、剧场
_TV_CATEGORY_DRAMA = re.compile(r"(?<![喜悲正闹])[剧劇](?![情场場])")


def infer_item_kind(configured_kind, title, item):
    _, _, explicit = parse_title(title)
    # 仅明确的"第N季"/Season N 才强制判为剧集；"第N部"常见于电影续集，不能据此判定
    if explicit:
        return "tv"
    # 剧场版/大电影是电影，即使挂在"动漫"等剧集分类下
    if re.search(r"剧场版|劇場版|大电影|大電影", str(title or "")):
        return "movie"
    category = str(item.get("type_name") or "").lower()
    # 先判电影：避免"动漫电影""电视电影"被"动漫""电视"类词误判为剧集
    if any(token in category for token in _MOVIE_CATEGORY_TOKENS):
        return "movie"
    if any(token in category for token in _TV_CATEGORY_TOKENS) or _TV_CATEGORY_DRAMA.search(category):
        return "tv"
    for key in ("vod_total", "vod_serial"):
        try:
            if int(item.get(key) or 0) > 1:
                return "tv"
        except (TypeError, ValueError):
            continue
    remarks = str(item.get("vod_remarks") or "").lower()
    if re.search(r"(?:更新至|全|共)\s*\d+\s*集|第\s*\d+\s*集", remarks):
        return "tv"
    for _line_name, episodes in parse_play_lines(item):
        episode_names = [name.strip() for name, _url in episodes]
        if any(
            re.search(
                r"(?:第\s*[0-9一二三四五六七八九十两]+\s*[集话話期]"
                r"|(?:EP|E)\s*0*[0-9]+)",
                name,
                re.IGNORECASE,
            )
            for name in episode_names
        ):
            return "tv"
        # 两条及以上、名称全是纯数字序号，视为分集（单条或"HD/国语"这类版本名不算）
        if len(episode_names) >= 2 and all(episode_number(name) is not None for name in episode_names):
            return "tv"
    return configured_kind


SKIP_EPISODE_WORDS = ("预告", "花絮", "特辑")
_EP_NAME = re.compile(
    r"^\s*(?:第\s*([0-9]{1,4}|[一二三四五六七八九十两零〇]{1,3})\s*[集话話期].*"
    r"|(?:EP|E)\s*0*([0-9]{1,4})(?![0-9]).*"
    r"|0*([0-9]{1,4}))\s*$",
    re.IGNORECASE,
)


def parse_play_lines(record, skip_words=SKIP_EPISODE_WORDS):
    """解析 CMS 记录的 vod_play_url/vod_play_from，返回 [(线路名, [(集名, url)])]。

    只保留 http(s) 的 m3u8 地址，并丢弃预告/花絮/特辑等非正片条目；
    所有脚本统一使用本函数，避免各处各写一份解析逻辑。
    """
    groups = str(record.get("vod_play_url") or "").split("$$$")
    froms = str(record.get("vod_play_from") or "").split("$$$")
    lines = []
    for index, group in enumerate(groups):
        episodes = []
        for entry in group.split("#"):
            if "$" not in entry:
                continue
            name, url = entry.split("$", 1)
            url = url.strip()
            lowered = url.lower()
            if ".m3u8" not in lowered or not lowered.startswith(("http://", "https://")):
                continue
            if any(word in name for word in skip_words):
                continue
            episodes.append((name, url))
        if episodes:
            line_name = froms[index].strip() if index < len(froms) else ""
            lines.append((line_name or f"线路{index}", episodes))
    return lines


def episode_number(name):
    """从集名解析集号（第N集 / EPN / 纯数字）；无法解析返回 None。"""
    match = _EP_NAME.match(str(name or ""))
    if not match:
        return None
    token = match.group(1) or match.group(2) or match.group(3)
    return cn2int(token) if token else None


def number_episodes(eps):
    """[(集名, url)] -> [(集号, url)]。

    集名都能解析且不重复时按集名编号（源里缺集、有番外时不会错位）；
    否则按顺序从 1 编号。续集季若沿用上一季的累计集号（如第13-24集），
    且连续无缺，则从 1 重新编号。
    """
    eps = list(eps)
    numbers = [episode_number(name) for name, _ in eps]
    if eps and None not in numbers and len(set(numbers)) == len(numbers):
        low, high = min(numbers), max(numbers)
        if low > 1 and high - low + 1 == len(numbers):
            numbers = [number - low + 1 for number in numbers]
        return [(number, url) for number, (_, url) in zip(numbers, eps)]
    return [(index, url) for index, (_, url) in enumerate(eps, 1)]


def stage_write(path, text):
    """先写入同目录临时文件，返回临时路径；调用 os.replace 后才对外可见。"""
    descriptor, temporary = tempfile.mkstemp(
        prefix=".strm-", suffix=".tmp", dir=os.path.dirname(path) or "."
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
        # mkstemp 默认 0600，Jellyfin 以其他用户运行时会读不到
        os.chmod(temporary, 0o644)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    return temporary


def atomic_write(path, text):
    """原子写文件：Jellyfin 扫描时不会读到写了一半的 .strm。"""
    temporary = stage_write(path, text)
    try:
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def write_strm(r):
    """r: name(剧名/片名), full(带季标记的完整名), season, explicit, year, eps"""
    eps, year = r["eps"], r.get("year", "")
    if not is_tv(r):
        name = r.get("full") or r["name"]
        folder = f"{name} ({year})" if year else name
        d = f"{MOVIE_ROOT}/{folder}"
        os.makedirs(d, exist_ok=True)
        atomic_write(f"{d}/{folder}.strm", eps[0][1] + "\n")
        return folder, len(eps)
    base, season = r["name"], r.get("season", 1)
    folder = find_series_dir(base, year, season)
    d = f"{TV_ROOT}/{folder}/Season {season:02d}"
    os.makedirs(d, exist_ok=True)
    for number, url in number_episodes(eps):
        atomic_write(f"{d}/{base} S{season:02d}E{number:02d}.strm", url + "\n")
    return f"{folder}/Season {season:02d}", len(eps)
