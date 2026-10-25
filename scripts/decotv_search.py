#!/usr/bin/env python3
import json
import urllib.error
import urllib.parse
import urllib.request


def has_admin_credentials(config):
    decotv = config.get("decotv", {})
    return bool((decotv.get("admin_username") or "").strip() and decotv.get("admin_password"))


def get_cookie(config, log=print, user_agent="Mozilla/5.0"):
    """用 DecoTV 管理员凭证登录并返回 Cookie；未配置或登录失败时回退到 config.json 的 cookie。"""
    if not has_admin_credentials(config):
        return config.get("cookie", "")
    decotv = config["decotv"]
    request = urllib.request.Request(
        (decotv.get("url") or "http://decotv:3000").rstrip("/") + "/api/login",
        data=json.dumps({
            "username": decotv["admin_username"].strip(),
            "password": decotv["admin_password"],
        }).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": user_agent},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            cookies = response.headers.get_all("Set-Cookie") or []
    except (OSError, urllib.error.URLError) as error:
        log("DecoTV 登录失败:", type(error).__name__)
        return config.get("cookie", "")
    cookie = "; ".join(
        part.split(";", 1)[0].strip() for part in cookies if part.split(";", 1)[0].strip()
    )
    if not cookie:
        log("DecoTV 登录成功但未返回 Cookie")
    return cookie or config.get("cookie", "")


def get_sources(config, cookie, http_json, log=print):
    decotv = config.get("decotv", {})
    if not (decotv.get("admin_username") and decotv.get("admin_password")):
        log("未配置 DecoTV 管理员凭证，搜索使用 config.json 的 cms_apis")
        return [
            {"name": urllib.parse.urlparse(api).netloc, "api": api}
            for api in config.get("cms_apis", [])
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
    log("从 DecoTV 读取到可搜索来源数:", len(sources))
    return sources


def _as_decotv_aggregate_record(item):
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None

    episodes = item.get("episodes")
    episode_titles = item.get("episodes_titles") or []
    if not isinstance(episodes, list):
        return None

    urls = []
    for idx, url in enumerate(episodes):
        if not isinstance(url, str):
            continue
        cleaned = url.strip()
        if cleaned and ".m3u8" in cleaned.lower():
            urls.append(cleaned)
    if not urls:
        return None

    vp = []
    for idx, url in enumerate(urls):
        title_text = ""
        if isinstance(episode_titles, list) and idx < len(episode_titles):
            title_text = str(episode_titles[idx]).strip()
        if not title_text:
            title_text = str(idx + 1)
        vp.append(f"{title_text}${url}")
    source_name = str(item.get("source_name") or item.get("source") or "DecoTV").strip()
    return {
        "vod_name": title,
        "vod_year": str(item.get("year") or ""),
        "type_name": str(item.get("type_name") or ""),
        "vod_remarks": str(item.get("remarks") or ""),
        "vod_play_url": "#".join(vp),
        "vod_play_from": source_name,
        "_source_name": source_name,
    }


def search_all(config, title, sources, cookie, http_json, log=print):
    decotv = config.get("decotv", {})
    decotv_url = (decotv.get("url") or "http://decotv:3000").rstrip("/")
    if decotv.get("admin_username") and decotv.get("admin_password"):
        query = urllib.parse.urlencode({"q": title, "adfilter": "direct"})
        search_url = decotv_url + "/api/search?" + query
        log("搜索来源:", "DecoTV 聚合搜索", "DecoTV")
        try:
            payload = http_json(search_url, cookie)
        except Exception as error:
            log("DecoTV 聚合搜索失败:", type(error).__name__)
            return []
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            log("DecoTV 聚合搜索响应格式异常")
            return []

        results = []
        seen = set()
        for item in payload["results"]:
            record = _as_decotv_aggregate_record(item)
            if record is None:
                continue
            key = (record["vod_name"], record["vod_play_url"], record["vod_play_from"])
            if key in seen:
                continue
            seen.add(key)
            source = {"name": record["_source_name"], "api": "decotv://aggregate"}
            results.append((source, record))
        log("CMS 搜索返回记录数:", "DecoTV", len(results))
        return results

    query = urllib.parse.urlencode({"ac": "detail", "wd": title})
    results = []
    for source in sources:
        api = source["api"]
        target = f"{api}{'&' if '?' in api else '?'}{query}"
        proxy_url = decotv_url + "/api/proxy/cms?url=" + urllib.parse.quote(target, safe="")
        host = urllib.parse.urlparse(api).netloc
        log("搜索来源:", source.get("name") or host, host)
        try:
            payload = http_json(proxy_url, cookie)
        except Exception as error:
            log("搜索失败:", host, type(error).__name__)
            continue
        items = payload.get("list", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            log("CMS 搜索响应格式异常:", host)
            continue
        log("CMS 搜索返回记录数:", host, len(items))
        results.extend((source, item) for item in items if isinstance(item, dict))
    return results
