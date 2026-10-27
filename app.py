import atexit
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for
from waitress import serve


DATA_DIR = Path(os.environ.get("STRM_DATA_DIR", "/data"))
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = DATA_DIR / "config.json"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
ADMIN_USER = os.environ.get("STRM_ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("STRM_ADMIN_PASSWORD", "")
SESSION_SECRET = os.environ.get("STRM_SESSION_SECRET", "")
CONFIG_LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
BOT_LOCK = threading.Lock()
RUNTIME = {"job": None, "bot": "未运行", "last_job": "尚无定时任务"}
BOT_PROCESS = None
BOT_SIGNATURE = None
STOP_EVENT = threading.Event()
DRY_RUN_TIMEOUT = 300

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))
app.secret_key = SESSION_SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.environ.get("STRM_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=8 * 60 * 60,
    MAX_CONTENT_LENGTH=1024 * 1024,
)

SECRET_KEYS = {"password", "admin_password", "token", "cookie", "api_key"}
SECRET_MASK = "********"
EXPECTED_OBJECTS = {"jellyfin", "telegram", "recheck", "crawl", "decotv", "scheduler"}
EXPECTED_LISTS = {"list_urls", "cms_apis"}
CRAWL_SKIP_REMARK_OPTIONS = [
    ("TC", "TC（枪版）"),
    ("TS", "TS（枪版）"),
    ("CAM", "CAM（枪版）"),
    ("枪版", "枪版"),
    ("抢先", "抢先"),
    ("预告", "预告"),
]
NUMBER_FIELDS = {
    "max_new_per_run": (0, 100000),
    "max_pages": (0, 100000),
    "max_lookups_per_run": (0, 1000000),
    "retry_miss_days": (0, 36500),
}


def load_config():
    with CONFIG_LOCK:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            value = json.load(config_file)
    if not isinstance(value, dict):
        raise ValueError("config.json 顶层必须是 JSON 对象")
    return value


def secret_values(value):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in SECRET_KEYS and isinstance(child, str) and child:
                found.append(child)
            found.extend(secret_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(secret_values(child))
    return found


def redact(text):
    try:
        values = secret_values(load_config())
    except (OSError, ValueError, json.JSONDecodeError):
        values = []
    for value in sorted(set(values), key=len, reverse=True):
        text = text.replace(value, "********")
    return text


def hide_secrets(value):
    if isinstance(value, dict):
        return {
            key: (SECRET_MASK if key.lower() in SECRET_KEYS and child else hide_secrets(child))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [hide_secrets(child) for child in value]
    return value


def restore_blank_secrets(candidate, current):
    if isinstance(candidate, dict) and isinstance(current, dict):
        for key, old_value in current.items():
            if key.lower() in SECRET_KEYS and old_value:
                if key not in candidate or candidate[key] == "":
                    candidate[key] = old_value
                elif isinstance(candidate[key], (dict, list)):
                    candidate[key] = restore_blank_secrets(candidate[key], old_value)
            elif key in candidate:
                candidate[key] = restore_blank_secrets(candidate[key], old_value)
    elif isinstance(candidate, list) and isinstance(current, list):
        for index, old_value in enumerate(current[:len(candidate)]):
            candidate[index] = restore_blank_secrets(candidate[index], old_value)
    return candidate


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("JSON 顶层必须是对象")
    for key in EXPECTED_OBJECTS:
        if key in config and not isinstance(config[key], dict):
            if key not in {"login", "telegram"} or config[key] is not None:
                raise ValueError(f"{key} 必须是对象" + ("或 null" if key in {"login", "telegram"} else ""))
    for key in EXPECTED_LISTS:
        if key in config and not isinstance(config[key], list):
            raise ValueError(f"{key} 必须是数组")
        if key in config and any(not isinstance(item, str) for item in config[key]):
            raise ValueError(f"{key} 的成员必须是字符串")
    for key, (minimum, maximum) in NUMBER_FIELDS.items():
        value = config.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} 必须是数字")
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
    if "jellyfin" in config and isinstance(config["jellyfin"], dict):
        for key in ("url", "api_key"):
            value = config["jellyfin"].get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"jellyfin.{key} 必须是字符串")
    if "decotv" in config and isinstance(config["decotv"], dict):
        for key in ("url", "admin_username", "admin_password"):
            value = config["decotv"].get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"decotv.{key} 必须是字符串")
    if "telegram" in config and isinstance(config["telegram"], dict):
        token = config["telegram"].get("token")
        if token is not None and not isinstance(token, str):
            raise ValueError("telegram.token 必须是字符串")
        ids = config["telegram"].get("allowed_chat_ids", [])
        if not isinstance(ids, list) or any(
            isinstance(item, bool) or not isinstance(item, (int, str)) for item in ids
        ):
            raise ValueError("telegram.allowed_chat_ids 必须是数字或字符串数组")
    if "crawl" in config and isinstance(config["crawl"], dict):
        crawl = config["crawl"]
        for key in ("max_age_years", "min_year", "min_score", "max_new_per_run",
                    "max_pages_per_run", "per_type_new", "per_type_pages",
                    "backfill_max_pages", "update_pages", "max_eps", "delay"):
            value = crawl.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ValueError(f"crawl.{key} 必须是数字")
        for key in ("skip_unknown_year",):
            value = crawl.get(key)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"crawl.{key} 必须是 true 或 false")
        score_field = crawl.get("score_field")
        if score_field is not None and score_field not in {"douban", "vod", "any"}:
            raise ValueError("crawl.score_field 只能是 douban、vod 或 any")
        remarks = crawl.get("skip_remarks")
        if remarks is not None and (
            not isinstance(remarks, list) or any(not isinstance(item, str) for item in remarks)
        ):
            raise ValueError("crawl.skip_remarks 必须是字符串数组")
        sources = crawl.get("sources", [])
        if not isinstance(sources, list) or any(not isinstance(item, dict) for item in sources):
            raise ValueError("crawl.sources 必须是对象数组")
        for index, source in enumerate(sources):
            for key in ("name", "api"):
                if key in source and not isinstance(source[key], str):
                    raise ValueError(f"crawl.sources[{index}].{key} 必须是字符串")
            types = source.get("types", {})
            if not isinstance(types, dict) or any(kind not in {"movie", "tv"} for kind in types.values()):
                raise ValueError(f"crawl.sources[{index}].types 的值只能是 movie 或 tv")
    if "recheck" in config and isinstance(config["recheck"], dict):
        for key in ("min_headroom", "switch_gain", "interval_days", "max_per_run"):
            value = config["recheck"].get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            ):
                raise ValueError(f"recheck.{key} 必须是非负数字")
    if "scheduler" in config and isinstance(config["scheduler"], dict):
        scheduler = config["scheduler"]
        if "enabled" in scheduler and not isinstance(scheduler["enabled"], bool):
            raise ValueError("scheduler.enabled 必须是 true 或 false")
        if "notifications_enabled" in scheduler and not isinstance(
            scheduler["notifications_enabled"], bool
        ):
            raise ValueError("scheduler.notifications_enabled 必须是 true 或 false")
        for job_name in ("crawl", "recheck", "stats"):
            job = scheduler.get(job_name)
            if job is None:
                continue
            if not isinstance(job, dict):
                raise ValueError(f"scheduler.{job_name} 必须是对象")
            if "enabled" in job and not isinstance(job["enabled"], bool):
                raise ValueError(f"scheduler.{job_name}.enabled 必须是 true 或 false")
            if "script" in job and job["script"] not in {"auto", "crawl"}:
                raise ValueError(f"scheduler.{job_name}.script 只能是 auto 或 crawl")
            if "hour" in job and (not isinstance(job["hour"], int) or not 0 <= job["hour"] <= 23):
                raise ValueError(f"scheduler.{job_name}.hour 必须是 0 到 23 的整数")
            if "minute" in job and (not isinstance(job["minute"], int) or not 0 <= job["minute"] <= 59):
                raise ValueError(f"scheduler.{job_name}.minute 必须是 0 到 59 的整数")
            if "weekday" in job and (not isinstance(job["weekday"], int) or not 0 <= job["weekday"] <= 6):
                raise ValueError(f"scheduler.{job_name}.weekday 必须是 0 到 6 的整数")
    return config


SCHEDULE_WINDOWS = {
    "crawl": (1, 3),
    "recheck": (5, 6),
    "stats": (8, 11),
}


def fill_schedule_defaults(config, rng=random):
    """Give each job a random run time once, so installs don't all fire together."""
    scheduler = config.get("scheduler")
    if not isinstance(scheduler, dict):
        scheduler = config["scheduler"] = {}
    changed = False
    for name, (first_hour, last_hour) in SCHEDULE_WINDOWS.items():
        job = scheduler.get(name)
        if not isinstance(job, dict):
            job = scheduler[name] = {}
            changed = True
        if "hour" not in job or "minute" not in job:
            job["hour"] = rng.randint(first_hour, last_hour)
            job["minute"] = rng.randint(0, 59)
            changed = True
        if name == "stats" and "weekday" not in job:
            job["weekday"] = rng.randint(0, 6)
            changed = True
    return changed


def ensure_schedule_defaults():
    try:
        config = load_config()
        if fill_schedule_defaults(config):
            save_config(config)
    except (OSError, ValueError):
        pass


def save_config(config):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_LOCK:
        if CONFIG_PATH.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = BACKUP_DIR / f"config-{stamp}.json"
            backup.write_bytes(CONFIG_PATH.read_bytes())
            os.chmod(backup, 0o600)
            backups = sorted(BACKUP_DIR.glob("config-*.json"))
            for old_backup in backups[:-10]:
                old_backup.unlink()
        temporary = CONFIG_PATH.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=2)
            config_file.write("\n")
            config_file.flush()
            os.fsync(config_file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, CONFIG_PATH)


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf():
    return {"csrf_token": csrf_token}


@app.before_request
def require_login():
    if request.endpoint == "login" or request.endpoint == "static":
        return None
    if not session.get("authenticated"):
        return redirect(url_for("login", next=request.path))
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not sent or not expected or not hmac.compare_digest(sent, expected):
            return "CSRF 校验失败，请刷新页面重试。", 400
    return None


def parse_form_int(value, minimum=None, maximum=None):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(f"无效数字: {value}") from error
    if minimum is not None and parsed < minimum:
        raise ValueError(f"值不能小于 {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"值不能大于 {maximum}")
    if parsed.is_integer():
        return int(parsed)
    return parsed


def parse_form_json_list(value):
    if value is None:
        return []
    cleaned = value.strip()
    if not cleaned:
        return []
    if cleaned.startswith("[") or cleaned.startswith("{"):
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else [parsed]
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def parse_form_chat_ids(value):
    if value is None:
        return []
    cleaned = value.strip()
    if not cleaned:
        return []
    parsed = []
    for part in cleaned.split(","):
        part = part.strip()
        if not part:
            continue
        if not re.fullmatch(r"-?\d+", part):
            raise ValueError("telegram.allowed_chat_ids 请输入数字，多个数字请用逗号分隔")
        parsed.append(int(part))
    return parsed


def parse_form_selected_values(form, key, allowed_values):
    values = form.getlist(key) if hasattr(form, "getlist") else [form.get(key, "")]
    return [value for value, _label in allowed_values if value in values]


def parse_form_secret(form, key):
    value = form.get(key, "")
    return "" if value == SECRET_MASK else value


def infer_source_kind(type_name):
    text = str(type_name or "").lower()
    if not text:
        return "movie"
    movie_tokens = ("电影", "movie", "movies", "片", "影片", "动作", "剧情", "喜剧", "爱情", "科幻", "恐怖", "战争", "犯罪")
    tv_tokens = ("剧", "tv", "连续剧", "电视剧", "综艺", "动漫", "纪录片", "网络剧", "韩剧", "美剧", "日剧", "国产剧", "港剧")
    if any(token in text for token in movie_tokens):
        return "movie"
    if any(token in text for token in tv_tokens):
        return "tv"
    if any(token in text for token in ("综艺", "动漫", "纪录", "演唱会")):
        return "tv"
    return "movie"


def extract_class_entries(payload):
    if isinstance(payload, dict):
        for key in ("class", "classes", "types", "category", "categories", "list"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return candidate
            if isinstance(candidate, dict):
                nested = candidate.get("list")
                if isinstance(nested, list):
                    return nested
                nested_values = list(candidate.values())
                if nested_values and isinstance(nested_values[0], list):
                    return nested_values[0]
        if isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload.get("result"), list):
            return payload["result"]
        if isinstance(payload.get("items"), list):
            return payload["items"]
    elif isinstance(payload, list):
        return payload
    return []


def discover_sources_from_cms_apis(api_urls):
    results = []
    for raw in api_urls or []:
        url = str(raw or "").strip()
        if not url:
            continue

        candidates = []
        base = url.rstrip("/")
        normalized = base
        if "ac=" not in base:
            normalized = base + ("?ac=list" if "?" in base else "?ac=list")
        if normalized not in candidates:
            candidates.append(normalized)
        if not base.endswith("/api.php/provide/vod") and "/provide/" in base:
            candidates.append(base + ("?ac=list" if "?" not in base else "&ac=list"))
        if not base.endswith("/"):
            candidates.append(base + "/?ac=list")
        else:
            candidates.append(base + "?ac=list")

        seen_urls = set()
        for candidate in candidates:
            if candidate in seen_urls:
                continue
            seen_urls.add(candidate)
            try:
                req = urllib.request.Request(candidate, headers={"User-Agent": "STRM-Manager/1.0"})
                with urllib.request.urlopen(req, timeout=15) as response:
                    payload = json.load(response)
            except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
                continue

            classes = extract_class_entries(payload)
            types = {}
            for index, item in enumerate(classes):
                if not isinstance(item, dict):
                    continue
                type_id = item.get("type_id")
                if type_id is None:
                    for key in ("id", "type", "tid"):
                        if key in item:
                            type_id = item.get(key)
                            break
                if type_id is None:
                    type_id = index
                name = item.get("type_name") or item.get("name") or item.get("cate_name") or item.get("typename") or item.get("title")
                if name is None:
                    continue
                types[str(type_id)] = infer_source_kind(name)
            if not types:
                continue
            host = urllib.parse.urlparse(url).netloc or "cms"
            results.append({
                "name": host,
                "api": base,
                "types": types,
            })
            break
    return results


def fetch_decotv_sources(config):
    decotv_url = (config.get("decotv") or {}).get("url") or "http://decotv:3000"
    parsed_url = urllib.parse.urlsplit(decotv_url.strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("DecoTV URL 必须是有效的 HTTP 或 HTTPS 地址")
    if parsed_url.username or parsed_url.password:
        raise ValueError("DecoTV URL 不应包含用户名或密码")

    base_url = urllib.parse.urlunsplit((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path.rstrip("/"),
        "",
        "",
    ))
    username = ((config.get("decotv") or {}).get("admin_username") or "").strip()
    password = (config.get("decotv") or {}).get("admin_password", "")
    if not username or not password:
        raise ValueError(
            "尚未配置 DecoTV 管理员凭证。请在本页下方填写管理员用户名和密码，"
            "点击“校验并保存”后刷新页面以同步视频源。"
        )

    login_url = base_url + "/api/login"
    login_body = json.dumps(
        {"username": username, "password": password}
    ).encode("utf-8")
    login_request = urllib.request.Request(
        login_url,
        data=login_body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(login_request, timeout=20) as response:
            cookies = response.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as error:
        raise ValueError(f"DecoTV 管理员登录失败：HTTP {error.code}。请检查管理员账号和密码。") from error

    cookie = "; ".join(
        part.split(";", 1)[0].strip() for part in cookies if part.split(";", 1)[0].strip()
    )
    if not cookie:
        raise ValueError("DecoTV 登录成功但没有返回 Cookie，无法读取管理员配置。")

    config_url = base_url + "/api/admin/config"
    config_request = urllib.request.Request(
        config_url,
        headers={
            "Accept": "application/json",
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(config_request, timeout=20) as response:
            content = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        raise ValueError(
            f"DecoTV 管理员登录后读取配置失败：HTTP {error.code}。"
            "请确认该账号拥有 DecoTV 管理员权限。"
        ) from error
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("DecoTV 配置响应超过允许大小")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("DecoTV 配置响应格式无效")
    app_config = payload.get("Config")
    source_config = app_config.get("SourceConfig") if isinstance(app_config, dict) else None
    if not isinstance(source_config, list):
        raise ValueError("DecoTV 配置中未找到 SourceConfig")
    if not source_config:
        raise ValueError(
            "DecoTV 管理配置中尚未添加视频源。请先登录 DecoTV 管理界面添加并启用视频源。"
        )

    sources = []
    seen_apis = set()
    for item in source_config:
        if (
            not isinstance(item, dict)
            or item.get("disabled")
            or item.get("is_adult")
        ):
            continue
        api = str(item.get("api") or "").strip()
        api_url = urllib.parse.urlsplit(api)
        if api_url.scheme not in {"http", "https"} or not api_url.netloc or api in seen_apis:
            continue
        name = str(item.get("name") or item.get("key") or api_url.netloc).strip()
        if not name:
            name = api_url.netloc
        sources.append({"name": name, "api": api})
        seen_apis.add(api)
    if not sources:
        raise ValueError(
            "DecoTV 中没有可用的已启用非成人视频源。请检查来源是否已启用且 API 地址有效；"
            "禁用源和成人源不会显示。"
        )
    return sources


def merge_config(base, overlay):
    if isinstance(base, dict) and isinstance(overlay, dict):
        result = dict(base)
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge_config(result[key], value)
            else:
                result[key] = value
        return result
    if isinstance(base, list) and isinstance(overlay, list):
        return overlay
    return overlay if overlay is not None else base


def config_from_form(form):
    config = {
        "decotv": {
            "url": form.get("decotv_url", ""),
            "admin_username": form.get("decotv_admin_username", "").strip(),
            "admin_password": parse_form_secret(form, "decotv_admin_password"),
        },
        "max_new_per_run": parse_form_int(form.get("max_new_per_run", ""), 0, 100000),
        "max_pages": parse_form_int(form.get("max_pages", ""), 0, 100000),
        "max_lookups_per_run": parse_form_int(form.get("max_lookups_per_run", ""), 0, 1000000),
        "retry_miss_days": parse_form_int(form.get("retry_miss_days", ""), 0, 36500),
        "jellyfin": {
            "url": form.get("jellyfin_url", ""),
            "api_key": parse_form_secret(form, "jellyfin_api_key"),
        },
        "telegram": {
            "token": parse_form_secret(form, "telegram_token"),
            "allowed_chat_ids": parse_form_chat_ids(form.get("telegram_allowed_chat_ids", "")),
        },
        "recheck": {
            "min_headroom": parse_form_int(form.get("recheck_min_headroom", ""), 0, 1000),
            "switch_gain": parse_form_int(form.get("recheck_switch_gain", ""), 0, 1000),
            "interval_days": parse_form_int(form.get("recheck_interval_days", ""), 0, 3650),
            "max_per_run": parse_form_int(form.get("recheck_max_per_run", ""), 0, 100000),
        },
        "crawl": {
            "sources": parse_form_json_list(form.get("crawl_sources", "[]")),
            "max_age_years": parse_form_int(form.get("crawl_max_age_years", ""), 0, 1000),
            "min_year": parse_form_int(form.get("crawl_min_year", ""), 0, 10000),
            "skip_unknown_year": form.get("crawl_skip_unknown_year") == "on",
            "min_score": parse_form_int(form.get("crawl_min_score", ""), 0, 100),
            "score_field": form.get("crawl_score_field", "douban") or "douban",
            "skip_remarks": parse_form_selected_values(
                form, "crawl_skip_remarks", CRAWL_SKIP_REMARK_OPTIONS
            ),
            "max_new_per_run": parse_form_int(form.get("crawl_max_new_per_run", ""), 0, 100000),
            "max_pages_per_run": parse_form_int(form.get("crawl_max_pages_per_run", ""), 0, 100000),
            "per_type_new": parse_form_int(form.get("crawl_per_type_new", ""), 0, 100000),
            "per_type_pages": parse_form_int(form.get("crawl_per_type_pages", ""), 0, 100000),
            "backfill_max_pages": parse_form_int(form.get("crawl_backfill_max_pages", ""), 0, 100000),
            "update_pages": parse_form_int(form.get("crawl_update_pages", ""), 0, 100000),
            "max_eps": parse_form_int(form.get("crawl_max_eps", ""), 0, 1000),
            "delay": parse_form_int(form.get("crawl_delay", ""), 0, 10000),
        },
        "scheduler": {
            "enabled": form.get("scheduler_enabled") == "on",
            "notifications_enabled": form.get("scheduler_notifications_enabled") == "on",
            "crawl": {
                "enabled": form.get("scheduler_crawl_enabled") == "on",
                "script": form.get("scheduler_crawl_script", "auto") or "auto",
                "hour": parse_form_int(form.get("scheduler_crawl_hour", ""), 0, 23),
                "minute": parse_form_int(form.get("scheduler_crawl_minute", ""), 0, 59),
            },
            "recheck": {
                "enabled": form.get("scheduler_recheck_enabled") == "on",
                "hour": parse_form_int(form.get("scheduler_recheck_hour", ""), 0, 23),
                "minute": parse_form_int(form.get("scheduler_recheck_minute", ""), 0, 59),
            },
            "stats": {
                "enabled": form.get("scheduler_stats_enabled") == "on",
                "weekday": parse_form_int(form.get("scheduler_stats_weekday", ""), 0, 6),
                "hour": parse_form_int(form.get("scheduler_stats_hour", ""), 0, 23),
                "minute": parse_form_int(form.get("scheduler_stats_minute", ""), 0, 59),
            },
        },
    }
    if "cms_apis" in form:
        config["cms_apis"] = parse_form_json_list(form.get("cms_apis", ""))
    if "list_urls" in form:
        config["list_urls"] = parse_form_json_list(form.get("list_urls", ""))
    if config["recheck"]["min_headroom"] is None:
        config["recheck"].pop("min_headroom")
    if config["recheck"]["switch_gain"] is None:
        config["recheck"].pop("switch_gain")
    if config["recheck"]["interval_days"] is None:
        config["recheck"].pop("interval_days")
    if config["recheck"]["max_per_run"] is None:
        config["recheck"].pop("max_per_run")
    for key in list(config.keys()):
        if key in {"max_new_per_run", "max_pages", "max_lookups_per_run", "retry_miss_days"} and config[key] is None:
            config.pop(key)
    for key in list(config["crawl"].keys()):
        if key == "skip_unknown_year":
            continue
        if config["crawl"][key] is None:
            config["crawl"].pop(key)
    scheduler = config.get("scheduler")
    if isinstance(scheduler, dict):
        for job_name in ("crawl", "recheck", "stats"):
            if job_name in scheduler and isinstance(scheduler[job_name], dict):
                for key in list(scheduler[job_name].keys()):
                    if scheduler[job_name][key] is None:
                        scheduler[job_name].pop(key)
        if not scheduler.get("crawl") and not scheduler.get("recheck") and not scheduler.get("stats"):
            config.pop("scheduler")
    return config


def http_status(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "STRM-Manager/1.0"})
        with urllib.request.urlopen(req, timeout=3) as response:
            return f"正常（HTTP {response.status}）"
    except (OSError, urllib.error.URLError, ValueError) as error:
        return f"不可用：{error}"


def test_service_connections(config):
    results = []
    decotv_url = (config.get("decotv", {}).get("url") or "http://decotv:3000").rstrip("/")
    jellyfin_url = (config.get("jellyfin", {}).get("url") or "http://jellyfin:8096").rstrip("/")
    decotv_config = config.get("decotv") or {}
    login_url = decotv_url + "/api/login"
    username = (decotv_config.get("admin_username") or "").strip()
    password = decotv_config.get("admin_password", "")

    if decotv_url:
        results.append(f"DecoTV：{http_status(decotv_url + '/')}" )
    if login_url and username and password:
        try:
            payload = json.dumps({"username": username, "password": password}).encode("utf-8")
            request = urllib.request.Request(
                login_url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "STRM-Manager/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                results.append(f"DecoTV 登录：正常（HTTP {response.status}）")
        except (OSError, urllib.error.URLError, ValueError) as error:
            results.append(f"DecoTV 登录：不可用：{error}")
    if jellyfin_url:
        results.append(f"Jellyfin：{http_status(jellyfin_url + '/health')}")
    try:
        with socket.create_connection(("decotv-redis", 6379), timeout=3) as conn:
            conn.sendall(b"*1\r\n$4\r\nPING\r\n")
            reply = conn.recv(64)
        results.append("Redis：正常（PONG）" if b"PONG" in reply else "Redis：不可用：Redis 未返回 PONG")
    except OSError as error:
        results.append(f"Redis：不可用：{error}")
    return "\n".join(results)


def services_status(config):
    decotv = config.get("decotv", {}).get("url", "http://decotv:3000").rstrip("/")
    jellyfin = config.get("jellyfin", {}).get("url", "http://jellyfin:8096").rstrip("/")
    result = {
        "DecoTV": http_status(decotv + "/"),
        "Jellyfin": http_status(jellyfin + "/health"),
    }
    try:
        with socket.create_connection(("decotv-redis", 6379), timeout=2) as conn:
            conn.sendall(b"*1\r\n$4\r\nPING\r\n")
            reply = conn.recv(64)
        result["Redis"] = "正常（PONG）" if b"PONG" in reply else "不可用：Redis 未返回 PONG"
    except OSError as error:
        result["Redis"] = f"不可用：{error}"
    return result


def tail_log(path, limit=60):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return "暂无日志。"
    return redact("\n".join(lines[-limit:]))


def run_dry_run(config):
    """同步运行 Dry Run 并返回结果；使用临时配置副本，不改动 config.json。"""
    handle, config_path = tempfile.mkstemp(prefix="dry-run-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as config_file:
            json.dump(config, config_file, ensure_ascii=False)
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", "/opt/m3u/crawl.py", "--dry"],
                cwd="/opt/m3u",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "STRM_CONFIG_PATH": config_path},
            )
        except OSError as error:
            return f"Dry Run 失败：无法启动 /opt/m3u/crawl.py（{error}）。"
        timed_out = False
        try:
            output, _ = process.communicate(timeout=DRY_RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            output, _ = process.communicate()
        lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
        summary = "\n".join(lines[-20:]) or "Dry Run 已执行，但没有输出。"
        if timed_out:
            return f"Dry Run 失败：模拟抓取超时（{DRY_RUN_TIMEOUT} 秒），已终止。以下为超时前的输出：\n{summary}"
        return f"Dry Run 完成（退出码 {process.returncode}）\n{summary}"
    finally:
        try:
            os.unlink(config_path)
        except OSError:
            pass


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not sent or not expected or not hmac.compare_digest(sent, expected):
            flash("登录表单已过期，请重试。", "error")
            return render_template("login.html"), 400
        username_ok = hmac.compare_digest(
            request.form.get("username", "").encode("utf-8"), ADMIN_USER.encode("utf-8")
        )
        password_ok = hmac.compare_digest(
            request.form.get("password", "").encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")
        )
        if username_ok and password_ok:
            session.clear()
            session["authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            session.permanent = True
            return redirect(url_for("index"))
        flash("用户名或密码不正确。", "error")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/decotv/sources")
def decotv_sources_api():
    try:
        config = load_config()
        sources = fetch_decotv_sources(config)
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            message = "连接 DecoTV 超时，请检查 DecoTV 地址、服务状态及容器网络。"
            reason_name = "timeout"
        else:
            message = "无法连接 DecoTV，请检查配置的 DecoTV URL 和 STRM Manager 容器网络。"
            reason_name = type(reason).__name__
        app.logger.warning("Unable to load DecoTV source list: %s", reason_name)
        return {"sources": [], "message": message}, 502
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
        if isinstance(error, (UnicodeDecodeError, json.JSONDecodeError)):
            message = "DecoTV 配置接口返回的内容不是有效 JSON/UTF-8。"
        elif isinstance(error, ValueError):
            message = str(error)
        else:
            message = "读取 DecoTV 配置失败，请检查 STRM Manager 日志和数据目录权限。"
        app.logger.warning("Unable to load DecoTV source list: %s", type(error).__name__)
        return {
            "sources": [],
            "message": message,
        }, 502
    if not sources:
        return {
            "sources": [],
            "message": (
                "DecoTV 中没有可用的已启用非成人视频源。请检查来源是否已启用且 API 地址有效；"
                "禁用源和成人源不会显示。"
            ),
        }, 502
    return {"sources": sources, "message": ""}


@app.route("/api/generate_sources", methods=["POST"])
def generate_sources_api():
    if not session.get("authenticated"):
        return {"error": "未登录"}, 401
    sent = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        return {"error": "CSRF 校验失败，请刷新页面重试。"}, 400
    api_urls = parse_form_json_list(request.form.get("cms_apis", ""))
    generated = discover_sources_from_cms_apis(api_urls)
    return {
        "count": len(generated),
        "message": (
            f"已生成 {len(generated)} 个源分类。"
            if generated
            else "未从 CMS 接口查询到有效分类，请检查 cms_apis 或接口返回格式。"
        ),
        "sources": generated,
    }


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            current = load_config()
            raw_config = request.form.get("config_json")
            if raw_config is not None and raw_config.strip():
                submitted = json.loads(raw_config)
            else:
                submitted = config_from_form(request.form)
            merged = merge_config(current, submitted)
            merged.pop("login", None)
            if isinstance(merged.get("crawl"), dict):
                merged["crawl"].pop("via_decotv", None)
            validate_config(merged)
            updated = restore_blank_secrets(merged, current)
            validate_config(updated)

            if action == "test_connection":
                message = test_service_connections(updated)
                session["last_action_result"] = message
                flash(message, "error" if "不可用：" in message or "失败" in message else "success")
            elif action == "dry_run":
                message = run_dry_run(updated)
                session["last_action_result"] = message
                flash(message, "error" if "失败" in message else "success")
            elif action == "generate_sources":
                generated = discover_sources_from_cms_apis(submitted.get("cms_apis", []))
                updated.setdefault("crawl", {})
                updated["crawl"]["sources"] = generated
                message = (
                    f"已生成 {len(generated)} 个源分类。"
                    if generated
                    else "未从 CMS 接口查询到有效分类，请检查 cms_apis 或接口返回格式。"
                )
                session["last_action_result"] = message
                flash(message, "success" if generated else "error")
            elif action.startswith("run_job_"):
                job_name = action.replace("run_job_", "")
                message = run_manual_job(job_name)
                session["last_action_result"] = message
                flash(message, "success")
            else:
                cms_apis_changed = updated.get("cms_apis", []) != current.get("cms_apis", [])
                if cms_apis_changed or (
                    not updated.get("crawl", {}).get("sources")
                    and updated.get("cms_apis")
                ):
                    generated = discover_sources_from_cms_apis(updated.get("cms_apis", []))
                    if generated:
                        updated.setdefault("crawl", {})
                        updated["crawl"]["sources"] = generated
                    elif cms_apis_changed:
                        raise ValueError(
                            "视频源已切换，但无法读取新源分类；配置未保存，请检查视频源地址后重试。"
                        )
                save_config(updated)
                session["last_action_result"] = "配置已校验并保存。定时任务和脚本下次运行时会读取新配置；Telegram 机器人会自动重启。"
                flash("配置已校验并保存。定时任务和脚本下次运行时会读取新配置；Telegram 机器人会自动重启。", "success")
        except (json.JSONDecodeError, ValueError) as error:
            flash(f"配置未保存：{error}", "error")
        except OSError as error:
            app.logger.exception("Unable to save config")
            flash(f"配置保存失败：{error}", "error")
        return redirect(url_for("index"))

    config_error = None
    try:
        config = load_config()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        config = {}
        config_error = str(error)
    with STATE_LOCK:
        runtime = dict(RUNTIME)
    last_action_result = session.pop("last_action_result", "")
    return render_template(
        "index.html",
        config=config,
        config_error=config_error,
        services=services_status(config) if not config_error else {},
        runtime=runtime,
        task_log=tail_log(LOG_DIR / "scheduler.log"),
        timezone=os.environ.get("TZ", "系统默认"),
        last_action_result=last_action_result,
        skip_remark_options=CRAWL_SKIP_REMARK_OPTIONS,
        config_json_view=json.dumps(hide_secrets(config), ensure_ascii=False, indent=2),
    )


def write_log(name, message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{name}.log"
    if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
        rotated = path.with_suffix(path.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
        path.replace(rotated)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{name}] {redact(message.rstrip())}\n"
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(line)


def scheduled_job(
    name, script, timeout, reclassify_after=False, wait=False,
    scheduled=False, notifications_enabled=False,
):
    if not JOB_LOCK.acquire(blocking=False):
        if not wait:
            write_log("scheduler", f"{name} 跳过：另一个定时任务仍在运行")
            return
        # 定时任务同一时刻到点时排队依次执行，而不是被丢弃
        write_log("scheduler", f"{name} 到点，等待其他任务完成后运行")
        while not JOB_LOCK.acquire(timeout=5):
            if STOP_EVENT.is_set():
                return
    with STATE_LOCK:
        RUNTIME["job"] = name
        RUNTIME["last_job"] = f"{name} 开始于 {datetime.now():%Y-%m-%d %H:%M:%S}"
    write_log("scheduler", f"开始运行 {name}")
    try:
        env = os.environ.copy()
        if scheduled:
            env["STRM_SCHEDULED_NOTIFICATIONS"] = (
                "1" if notifications_enabled else "0"
            )
        result = subprocess.run(
            [sys.executable, f"/opt/m3u/{script}"],
            cwd="/opt/m3u",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        output = result.stdout or ""
        for line in output.splitlines():
            write_log(name, line)
        outcome = "完成" if result.returncode == 0 else f"失败（退出码 {result.returncode}）"
        write_log("scheduler", f"{name}{outcome}")
        reclassify_outcome = ""
        if result.returncode == 0 and reclassify_after and script in {"auto.py", "crawl.py"}:
            with STATE_LOCK:
                RUNTIME["job"] = "电影分类整理"
            write_log("scheduler", "抓取完成，开始自动整理电影分类")
            try:
                organize_result = subprocess.run(
                    [sys.executable, "/opt/m3u/reclassify_media.py"],
                    cwd="/opt/m3u",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=2 * 60 * 60,
                    check=False,
                )
                for line in (organize_result.stdout or "").splitlines():
                    write_log("电影分类整理", line)
                reclassify_outcome = (
                    "；电影分类整理完成"
                    if organize_result.returncode == 0
                    else f"；电影分类整理失败（退出码 {organize_result.returncode}）"
                )
                write_log("scheduler", f"电影分类整理{reclassify_outcome[1:]}")
            except subprocess.TimeoutExpired:
                reclassify_outcome = "；电影分类整理超时"
                write_log("scheduler", "电影分类整理超时（7200 秒）")
            except OSError as error:
                reclassify_outcome = "；电影分类整理启动失败"
                write_log("scheduler", f"电影分类整理启动失败：{error}")
        with STATE_LOCK:
            RUNTIME["last_job"] = (
                f"{name}{outcome}{reclassify_outcome}，{datetime.now():%Y-%m-%d %H:%M:%S}"
            )
    except subprocess.TimeoutExpired:
        write_log("scheduler", f"{name} 超时终止（{timeout} 秒）")
        with STATE_LOCK:
            RUNTIME["last_job"] = f"{name} 超时"
    except OSError as error:
        write_log("scheduler", f"{name} 启动失败：{error}")
        with STATE_LOCK:
            RUNTIME["last_job"] = f"{name} 启动失败"
    finally:
        with STATE_LOCK:
            RUNTIME["job"] = None
        JOB_LOCK.release()


def run_manual_job(job_name):
    jobs = {
        "auto": ("默认分类抓取", "auto.py", 3 * 60 * 60),
        "crawl": ("指定源抓取", "crawl.py", 3 * 60 * 60),
        "recheck": ("线路复测", "recheck.py", 2 * 60 * 60),
        "stats": ("周统计", "stats.py", 2 * 60 * 60),
    }
    if job_name not in jobs:
        raise ValueError(f"不支持的任务: {job_name}")
    name, script, timeout = jobs[job_name]
    threading.Thread(target=scheduled_job, args=(name, script, timeout), daemon=True).start()
    return f"已启动任务：{name}"


def run_due_jobs(due_jobs):
    """按顺序依次运行同一时刻到点的定时任务。"""
    for job in due_jobs:
        if STOP_EVENT.is_set():
            return
        name, script, timeout, reclassify_after, *notification_setting = job
        scheduled_job(
            name,
            script,
            timeout,
            reclassify_after,
            wait=True,
            scheduled=True,
            notifications_enabled=(
                notification_setting[0] if notification_setting else False
            ),
        )


def scheduler_loop():
    started = {}
    while not STOP_EVENT.wait(10):
        try:
            config = load_config()
            scheduler_cfg = config.get("scheduler") or {}
            enabled = scheduler_cfg.get("enabled", True)
        except (OSError, ValueError, json.JSONDecodeError):
            enabled = False
        if not enabled:
            continue
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        started = {key: True for key in started if key.startswith(today)}
        jobs = {
            "auto": ("默认分类抓取", "auto.py", 3 * 60 * 60),
            "crawl": ("指定源抓取", "crawl.py", 3 * 60 * 60),
            "recheck": ("线路复测", "recheck.py", 2 * 60 * 60),
            "stats": ("周统计", "stats.py", 2 * 60 * 60),
        }
        due_jobs = []
        for job_name, job in jobs.items():
            if job_name == "auto" or job_name == "crawl":
                settings = (scheduler_cfg.get("crawl") or {})
                if settings.get("enabled") is False:
                    continue
                script_name = (settings.get("script") or "auto").lower()
                if job_name == "auto" and script_name != "auto":
                    continue
                if job_name == "crawl" and script_name != "crawl":
                    continue
                hour = settings.get("hour")
                minute = settings.get("minute")
                if hour is None or minute is None:
                    hour, minute = 2, 0
            else:
                settings = (scheduler_cfg.get(job_name) or {})
                if settings.get("enabled") is False:
                    continue
                hour = settings.get("hour")
                minute = settings.get("minute")
                weekday = settings.get("weekday")
                if hour is None or minute is None:
                    if job_name == "recheck":
                        hour, minute = 4, 30
                    elif job_name == "stats":
                        hour, minute = 9, 0
                if job_name == "stats" and weekday is not None and now.weekday() != weekday:
                    continue
            if now.hour == hour and now.minute == minute:
                key = f"{today}-{job_name}"
                if key in started:
                    continue
                started[key] = True
                due_jobs.append((
                    *job,
                    job_name in {"auto", "crawl"},
                    scheduler_cfg.get("notifications_enabled", False),
                ))
        if due_jobs:
            threading.Thread(
                target=run_due_jobs, args=(due_jobs,), name="scheduled-jobs", daemon=True
            ).start()


def stop_bot():
    global BOT_PROCESS
    with BOT_LOCK:
        process = BOT_PROCESS
        BOT_PROCESS = None
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def telegram_bot_signature(config):
    telegram = config.get("telegram") or {}
    token = telegram.get("token", "")
    allowed = telegram.get("allowed_chat_ids", [])
    if not token or not allowed:
        return None
    settings = {
        "telegram": [token, allowed],
        "cms_apis": config.get("cms_apis", []),
        "decotv": config.get("decotv", {}),
        "jellyfin": config.get("jellyfin", {}),
    }
    return hashlib.sha256(
        json.dumps(settings, sort_keys=True).encode("utf-8")
    ).hexdigest()


def bot_supervisor_loop():
    global BOT_PROCESS, BOT_SIGNATURE
    while not STOP_EVENT.is_set():
        try:
            config = load_config()
            signature = telegram_bot_signature(config)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            signature = None
            write_log("bot", f"读取配置失败：{error}")

        with BOT_LOCK:
            process = BOT_PROCESS
            old_signature = BOT_SIGNATURE
        if process and (signature != old_signature or process.poll() is not None):
            if process.poll() is not None:
                write_log("bot", f"机器人已退出，退出码 {process.returncode}")
            stop_bot()
            with BOT_LOCK:
                BOT_SIGNATURE = None
            process = None
        if signature and process is None and not STOP_EVENT.is_set():
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                logfile = (LOG_DIR / "bot.log").open("a", encoding="utf-8")
                process = subprocess.Popen(
                    [sys.executable, "/opt/m3u/bot.py"],
                    cwd="/opt/m3u",
                    stdout=logfile,
                    stderr=subprocess.STDOUT,
                )
                logfile.close()
                with BOT_LOCK:
                    BOT_PROCESS = process
                    BOT_SIGNATURE = signature
                write_log("bot", "Telegram 机器人已启动")
            except OSError as error:
                write_log("bot", f"机器人启动失败：{error}")
        with STATE_LOCK:
            RUNTIME["bot"] = "运行中" if signature and process and process.poll() is None else "未配置/未运行"
        STOP_EVENT.wait(5)


def shutdown():
    STOP_EVENT.set()
    stop_bot()


def handle_shutdown_signal(_signum, _frame):
    shutdown()
    raise SystemExit(0)


def main():
    if (
        not ADMIN_USER
        or len(ADMIN_PASSWORD) < 12
        or len(SESSION_SECRET) < 32
        or "CHANGE_ME" in ADMIN_PASSWORD.upper()
        or "CHANGE_ME" in SESSION_SECRET.upper()
    ):
        raise SystemExit(
            "请设置 STRM_ADMIN_USER、至少 12 位的 STRM_ADMIN_PASSWORD，"
            "以及至少 32 位的 STRM_SESSION_SECRET，且不能保留 CHANGE_ME 示例值。"
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ensure_schedule_defaults()
    atexit.register(shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()
    threading.Thread(target=bot_supervisor_loop, name="telegram-bot", daemon=True).start()
    serve(app, host="0.0.0.0", port=8080, threads=8)


if __name__ == "__main__":
    main()
