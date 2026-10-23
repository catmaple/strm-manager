#!/usr/bin/env python3
import os, re, sys, json, time, subprocess, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import probe
import seasons

CONFIG = "/opt/m3u/config.json"
UA = "Mozilla/5.0"
SKIP_WORDS = ("预告", "花絮", "特辑")

cfg = json.load(open(CONFIG, encoding="utf-8"))
TG = cfg["telegram"]
ALLOWED = {int(x) for x in TG.get("allowed_chat_ids", [])}
API = f"https://api.telegram.org/bot{TG['token']}/"
cache = {}  # chat_id -> 最近一次搜索的候选列表


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


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def candidates(title):
    tbase, tseason, _ = seasons.parse_title(title)
    out = {}
    for api in cfg["cms_apis"]:
        url = f"{api}{'&' if '?' in api else '?'}ac=detail&wd={urllib.parse.quote(tbase)}"
        try:
            data = http_json(url)
        except Exception as e:
            log("搜索失败:", api, e)
            continue
        host = urllib.parse.urlparse(api).netloc
        for v in data.get("list", []):
            raw = v.get("vod_name", "")
            tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
            if any(k in tags for k in SKIP_WORDS):
                continue
            base, sn, explicit = seasons.parse_title(raw)
            if not base:
                continue
            vy = (v.get("vod_year") or "").strip()
            vy = vy if vy.isdigit() else ""
            groups = v.get("vod_play_url", "").split("$$$")
            froms = v.get("vod_play_from", "").split("$$$")
            for i, g in enumerate(groups):
                if ".m3u8" not in g:
                    continue
                eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
                eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u]
                if not eps:
                    continue
                c = out.setdefault((base, sn, vy), {
                    "name": base, "full": seasons.clean(raw), "season": sn,
                    "explicit": explicit, "year": vy,
                    "lang": v.get("vod_lang") or "", "lines": []})
                ln = froms[i] if i < len(froms) else f"线路{i}"
                c["lines"].append({"label": f"{host}/{ln}", "eps": eps})
    res = list(out.values())
    for c in res:
        c["eps"] = max(c["lines"], key=lambda l: len(l["eps"]))["eps"]  # 仅用于显示
    res.sort(key=lambda c: not (c["name"] == tbase and c["season"] == tseason))
    return res[:8]


def label(c):
    tv = seasons.is_tv({"eps": c["eps"], "explicit": c["explicit"]})
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
        "explicit": c["explicit"], "year": c["year"], "eps": best["eps"]})
    ok = refresh_jellyfin()
    tail = "已通知 Jellyfin 扫描,稍等一两分钟在 VidHub 里刷新。" if ok \
        else "未配置 Jellyfin api_key,请等它定时扫描或手动扫描。"
    say(chat, f"✅ 已加入:{label(c)}\n线路:{best.get('label')}({best.get('_info', '')})\n目录:{folder}\n{tail}")


def search_and_reply(chat, title, direct=False):
    say(chat, f"正在搜索:{title}")
    cands = candidates(title)
    if not cands:
        say(chat, "没找到可用的 m3u8 资源,换个关键词或换个源试试。")
        return
    tb, ts, _ = seasons.parse_title(title)
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
        say(chat, "发送片名:搜索并选择\n/add 片名:同名唯一时直接加入\n"
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


main()
