#!/usr/bin/env python3
import os, re, sys, json, time, subprocess, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, "/opt/m3u")
import probe
import seasons

CONFIG = "/opt/m3u/config.json"
UA = "Mozilla/5.0"
SKIP_WORDS = ("预告", "花絮", "特辑")

cfg = {}
TG = {}
ALLOWED = set()
API = ""
cache = {}  # chat_id -> 最近一次搜索的候选列表


def get_decotv_cookie():
    decotv = cfg.get("decotv", {})
    username = (decotv.get("admin_username") or "").strip()
    password = decotv.get("admin_password", "")
    if not username or not password:
        cookie = cfg.get("cookie", "")
        if not cookie:
            log("缺少 DecoTV 管理员凭证，无法通过 DecoTV 代理搜索")
        return cookie

    login_url = decotv.get("url", "http://decotv:3000").rstrip("/") + "/api/login"
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        login_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            cookies = response.headers.get_all("Set-Cookie") or []
    except (OSError, urllib.error.URLError) as error:
        log("DecoTV 登录失败:", type(error).__name__)
        return cfg.get("cookie", "")

    cookie = "; ".join(
        part.split(";", 1)[0].strip()
        for part in cookies
        if part.split(";", 1)[0].strip()
    )
    if not cookie:
        log("DecoTV 登录成功但未返回 Cookie")
    return cookie or cfg.get("cookie", "")


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def tg(method, **params):
    req = urllib.request.Request(
        API + method, data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=65) as r:
        return json.load(r)


def say(chat, text, buttons=None):
    p = {"chat_id": chat, "text": text[:4000]}
    if buttons:
        p["reply_markup"] = {"inline_keyboard": buttons}
    try:
        tg("sendMessage", **p)
    except Exception as e:
        log("发送失败:", e)


def http_json(url, cookie=""):
    headers = {"User-Agent": UA}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def get_decotv_sources(cookie):
    decotv = cfg.get("decotv", {})
    if not (decotv.get("admin_username") and decotv.get("admin_password")):
        log("未配置 DecoTV 管理员凭证，Telegram 搜索仅使用 config.json 的 cms_apis")
        return [
            {"name": urllib.parse.urlparse(api).netloc, "api": api}
            for api in cfg.get("cms_apis", [])
            if isinstance(api, str) and api.strip()
        ]

    decotv_url = (decotv.get("url") or "http://decotv:3000").rstrip("/")
    try:
        payload = http_json(decotv_url + "/api/admin/config", cookie)
    except Exception as error:
        log("读取 DecoTV 视频源失败:", type(error).__name__)
        raise ValueError("无法读取 DecoTV 视频源配置，请检查管理员凭证和 DecoTV 服务状态") from error

    app_config = payload.get("Config") if isinstance(payload, dict) else None
    source_config = (
        app_config.get("SourceConfig") if isinstance(app_config, dict) else None
    )
    if not isinstance(source_config, list):
        raise ValueError("DecoTV 配置中未找到有效的视频源列表")

    sources = []
    seen_apis = set()
    for item in source_config:
        if not isinstance(item, dict) or item.get("disabled") or item.get("is_adult"):
            continue
        api = str(item.get("api") or "").strip()
        parsed_api = urllib.parse.urlsplit(api)
        if (
            parsed_api.scheme not in {"http", "https"}
            or not parsed_api.netloc
            or api in seen_apis
        ):
            continue
        name = str(item.get("name") or item.get("key") or parsed_api.netloc).strip()
        sources.append({"name": name or parsed_api.netloc, "api": api})
        seen_apis.add(api)
    if not sources:
        raise ValueError("DecoTV 中没有可搜索的已启用非成人视频源")
    return sources


def parse_search_title(raw):
    base, season, explicit = seasons.parse_title(raw)
    episode_match = re.search(
        r"(?:第\s*([0-9]{1,4}|[一二三四五六七八九十两]{1,3})\s*[集话話期]"
        r"|(?:EP|E)\s*0*([0-9]{1,4}))\s*$",
        base,
        re.IGNORECASE,
    )
    if not episode_match:
        return base, season, explicit, False
    base = re.sub(r"[\s:：\-—–·、,，]+$", "", base[:episode_match.start()]).strip()
    return base, season, explicit, True


def candidates(title):
    tbase, tseason, _, _ = parse_search_title(title)
    out = {}
    decotv = cfg.get("decotv", {})
    decotv_url = (decotv.get("url") or "http://decotv:3000").rstrip("/")
    cookie = get_decotv_cookie()
    for source in get_decotv_sources(cookie):
        api = source["api"]
        query = urllib.parse.urlencode({"ac": "detail", "wd": tbase})
        target = f"{api}{'&' if '?' in api else '?'}{query}"
        url = decotv_url + "/api/proxy/cms?url=" + urllib.parse.quote(target, safe="")
        try:
            data = http_json(url, cookie)
        except Exception as e:
            log("搜索失败:", api, e)
            continue
        host = urllib.parse.urlparse(api).netloc
        for v in data.get("list", []):
            raw = v.get("vod_name", "")
            tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
            if any(k in tags for k in SKIP_WORDS):
                continue
            base, sn, explicit, episode_marked = parse_search_title(raw)
            if not base:
                continue
            vy = (v.get("vod_year") or "").strip()
            vy = vy if vy.isdigit() else ""
            groups = v.get("vod_play_url", "").split("$$$")
            froms = v.get("vod_play_from", "").split("$$$")
            record_lines = []
            for i, g in enumerate(groups):
                eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
                eps = [
                    (n, u.strip())
                    for n, u in eps
                    if ".m3u8" in u.lower()
                ]
                if not eps:
                    continue
                ln = froms[i] if i < len(froms) else f"线路{i}"
                record_lines.append((ln, eps))
            if not record_lines:
                continue
            kind = seasons.infer_item_kind("movie", raw, v)
            c = out.setdefault((base, sn, vy), {
                "name": base, "full": seasons.clean(raw), "season": sn,
                "explicit": explicit, "year": vy,
                "kind": kind,
                "lang": v.get("vod_lang") or "", "lines": [],
                "record_count": 0, "episode_marked": False})
            c["record_count"] += 1
            c["episode_marked"] = c["episode_marked"] or episode_marked
            if kind == "tv" or episode_marked:
                c["kind"] = "tv"
            for ln, eps in record_lines:
                line_label = f"{host}/{ln}"
                line = next(
                    (entry for entry in c["lines"] if entry["label"] == line_label),
                    None,
                )
                if line is None:
                    line = {"label": line_label, "eps": []}
                    c["lines"].append(line)
                existing_urls = {url for _name, url in line["eps"]}
                line["eps"].extend(
                    (name, url) for name, url in eps if url not in existing_urls
                )
    res = list(out.values())
    season_groups = {}
    for c in res:
        season_groups.setdefault((c["name"], c["year"]), set()).add(c["season"])
    for c in res:
        if (
            (c["episode_marked"] and c["record_count"] > 1)
            or any(len(line["eps"]) > 1 for line in c["lines"])
            or len(season_groups[(c["name"], c["year"])]) > 1
        ):
            c["kind"] = "tv"
    for c in res:
        c["eps"] = max(c["lines"], key=lambda l: len(l["eps"]))["eps"]  # 仅用于显示
        c.pop("record_count", None)
        c.pop("episode_marked", None)
    res.sort(key=lambda c: not (c["name"] == tbase and c["season"] == tseason))
    return res[:8]


def label(c):
    tv = seasons.is_tv({"eps": c["eps"], "explicit": c["explicit"], "kind": c["kind"]})
    kind = f"第{c['season']}季 · {len(c['eps'])}集" if tv else "电影"
    nm = c["name"] if tv else c["full"]
    year = f" ({c['year']})" if c["year"] else ""
    lang = f" · {c['lang']}" if c["lang"] else ""
    return f"{nm}{year} · {kind}{lang}"


def refresh_jellyfin():
    jf = cfg.get("jellyfin", {})
    if not jf.get("api_key"):
        return False
    req = urllib.request.Request(
        jf["url"].rstrip("/") + "/Library/Refresh", method="POST",
        headers={"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        log("通知 Jellyfin 失败:", e)
        return False


def do_add(chat, c):
    say(chat, "正在测速,选择最快的线路……")
    best = probe.pick(c["lines"], log)
    folder, _ = seasons.write_strm({
        "name": c["name"], "full": c["full"], "season": c["season"],
        "explicit": c["explicit"], "year": c["year"], "kind": c["kind"],
        "eps": best["eps"]})
    ok = refresh_jellyfin()
    tail = "已通知 Jellyfin 扫描,稍等一两分钟在 VidHub 里刷新。" if ok \
        else "未配置 Jellyfin api_key,请等它定时扫描或手动扫描。"
    say(chat, f"✅ 已加入:{label(c)}\n线路:{best.get('label')}({best.get('_info', '')})\n目录:{folder}\n{tail}")


def search_and_reply(chat, title, direct=False):
    say(chat, f"正在搜索:{title}")
    try:
        cands = candidates(title)
    except ValueError as error:
        say(chat, str(error))
        return
    if not cands:
        say(chat, "没找到可用的 m3u8 资源,换个关键词或换个源试试。")
        return
    tb, ts, _, _ = parse_search_title(title)
    exact = [c for c in cands if c["name"] == tb and c["season"] == ts]
    if direct and len(exact) == 1:
        do_add(chat, exact[0])
        return
    cache[chat] = cands
    buttons = [[{"text": label(c)[:60], "callback_data": f"add:{i}"}]
               for i, c in enumerate(cands)]
    say(chat, "请选择要加入的版本:", buttons)


def run_script(chat, script, extra, title):
    say(chat, f"开始运行{title},可能需要几分钟……")
    try:
        r = subprocess.run([sys.executable, script] + extra,
                           capture_output=True, text=True, timeout=3600)
        lines = (r.stdout + r.stderr).strip().splitlines()
        say(chat, "完成:\n" + "\n".join(lines[-12:]))
    except Exception as e:
        say(chat, f"运行失败:{e}")


def handle(u):
    if "callback_query" in u:
        cq = u["callback_query"]
        chat = cq["message"]["chat"]["id"]
        if chat not in ALLOWED:
            return
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"])
        except Exception:
            pass
        data = cq.get("data", "")
        if data.startswith("add:"):
            cands = cache.get(chat) or []
            i = int(data[4:])
            if i < len(cands):
                do_add(chat, cands[i])
            else:
                say(chat, "列表已过期,请重新发送片名搜索。")
        return

    m = u.get("message")
    if not m:
        return
    chat = m["chat"]["id"]
    if chat not in ALLOWED:
        log("忽略未授权用户:", chat)
        return
    text = (m.get("text") or "").strip()
    if not text:
        return

    if text in ("/start", "/help"):
        say(chat, "直接发送片名:搜索并选择\n/add 片名:同名唯一时直接加入\n"
                  "/crawl:批量抓取一次\n/auto:榜单抓取\n/recheck:复测线路\n/stats 或 /status:查看统计\n/scan:通知 Jellyfin 扫描")
    elif text.startswith("/add"):
        title = text[4:].strip()
        if title:
            search_and_reply(chat, title, direct=True)
        else:
            say(chat, "用法:/add 片名")
    elif text == "/crawl":
        run_script(chat, "/opt/m3u/crawl.py", ["--new", "30"], "批量抓取")
    elif text == "/auto":
        run_script(chat, "/opt/m3u/auto.py", [], "榜单抓取")
    elif text == "/recheck":
        run_script(chat, "/opt/m3u/recheck.py", [], "线路复测")
    elif text in ("/stats", "/status"):
        try:
            r = subprocess.run([sys.executable, "/opt/m3u/stats.py", "--no-send"],
                               capture_output=True, text=True, timeout=120)
            say(chat, (r.stdout or r.stderr).strip() or "无输出")
        except Exception as e:
            say(chat, f"统计失败:{e}")
    elif text == "/scan":
        say(chat, "已通知 Jellyfin 扫描" if refresh_jellyfin() else "通知失败或未配置 api_key")
    elif text.startswith("/"):
        say(chat, "未知命令,发送 /help 查看用法。")
    else:
        search_and_reply(chat, text)


def main():
    global cfg, TG, ALLOWED, API
    with open(CONFIG, encoding="utf-8") as config_file:
        cfg = json.load(config_file)
    TG = cfg.get("telegram", {})
    ALLOWED = {int(x) for x in TG.get("allowed_chat_ids", [])}
    API = f"https://api.telegram.org/bot{TG.get('token', '')}/"
    log("机器人启动,授权用户:", sorted(ALLOWED))
    offset = None
    while True:
        params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            res = tg("getUpdates", **params)
        except Exception as e:
            log("getUpdates 失败:", e)
            time.sleep(5)
            continue
        for u in res.get("result", []):
            offset = u["update_id"] + 1
            try:
                handle(u)
            except Exception as e:
                log("处理出错:", e)


if __name__ == "__main__":
    main()

