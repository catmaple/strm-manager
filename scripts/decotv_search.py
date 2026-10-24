#!/usr/bin/env python3
import urllib.parse


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


def search_all(config, title, sources, cookie, http_json, log=print):
    query = urllib.parse.urlencode({"ac": "detail", "wd": title})
    decotv_url = (config.get("decotv", {}).get("url") or "http://decotv:3000").rstrip("/")
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
