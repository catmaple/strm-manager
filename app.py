import atexit
import hashlib
import hmac
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
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

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))
app.secret_key = SESSION_SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.environ.get("STRM_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=8 * 60 * 60,
    MAX_CONTENT_LENGTH=1024 * 1024,
)

SECRET_KEYS = {"password", "token", "cookie", "api_key"}
EXPECTED_OBJECTS = {"login", "jellyfin", "telegram", "recheck", "crawl", "decotv"}
EXPECTED_LISTS = {"list_urls", "cms_apis"}
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
            key: ("" if key.lower() in SECRET_KEYS and child else hide_secrets(child))
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
    if "login" in config and isinstance(config["login"], dict):
        for key in ("url", "username", "password"):
            value = config["login"].get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"login.{key} 必须是字符串")
    if "jellyfin" in config and isinstance(config["jellyfin"], dict):
        for key in ("url", "api_key"):
            value = config["jellyfin"].get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"jellyfin.{key} 必须是字符串")
    if "decotv" in config and isinstance(config["decotv"], dict):
        value = config["decotv"].get("url")
        if value is not None and not isinstance(value, str):
            raise ValueError("decotv.url 必须是字符串")
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
        for key in ("via_decotv", "skip_unknown_year"):
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
    return config


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


def http_status(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "STRM-Manager/1.0"})
        with urllib.request.urlopen(req, timeout=3) as response:
            return f"正常（HTTP {response.status}）"
    except (OSError, urllib.error.URLError, ValueError) as error:
        return f"不可用：{error}"


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


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            submitted = json.loads(request.form.get("config_json", ""))
            validate_config(submitted)
            current = load_config()
            updated = restore_blank_secrets(submitted, current)
            validate_config(updated)
            save_config(updated)
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
        editor_value = json.dumps(hide_secrets(config), ensure_ascii=False, indent=2)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        config = {}
        editor_value = "{}"
        config_error = str(error)
    with STATE_LOCK:
        runtime = dict(RUNTIME)
    return render_template(
        "index.html",
        config_json=editor_value,
        config_error=config_error,
        services=services_status(config) if not config_error else {},
        runtime=runtime,
        task_log=tail_log(LOG_DIR / "scheduler.log"),
        timezone=os.environ.get("TZ", "系统默认"),
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


def scheduled_job(name, script, timeout):
    if not JOB_LOCK.acquire(blocking=False):
        write_log("scheduler", f"{name} 跳过：另一个定时任务仍在运行")
        return
    with STATE_LOCK:
        RUNTIME["job"] = name
        RUNTIME["last_job"] = f"{name} 开始于 {datetime.now():%Y-%m-%d %H:%M:%S}"
    write_log("scheduler", f"开始运行 {name}")
    try:
        result = subprocess.run(
            [sys.executable, f"/opt/m3u/{script}"],
            cwd="/opt/m3u",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = result.stdout or ""
        for line in output.splitlines():
            write_log(name, line)
        outcome = "完成" if result.returncode == 0 else f"失败（退出码 {result.returncode}）"
        write_log("scheduler", f"{name}{outcome}")
        with STATE_LOCK:
            RUNTIME["last_job"] = f"{name}{outcome}，{datetime.now():%Y-%m-%d %H:%M:%S}"
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


def scheduler_loop():
    started = {}
    while not STOP_EVENT.wait(10):
        now = datetime.now()
        if now.hour == 2 and now.minute == 0:
            key, job = now.strftime("%Y-%m-%d") + "-crawl", ("批量抓取", "crawl.py", 3 * 60 * 60)
        elif now.hour == 4 and now.minute == 30:
            key, job = now.strftime("%Y-%m-%d") + "-recheck", ("线路复测", "recheck.py", 2 * 60 * 60)
        elif now.weekday() == 6 and now.hour == 9 and now.minute == 0:
            key, job = now.strftime("%Y-%m-%d") + "-stats", ("周统计", "stats.py", 2 * 60 * 60)
        else:
            continue
        if key not in started:
            started[key] = True
            threading.Thread(target=scheduled_job, args=job, daemon=True).start()


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


def bot_supervisor_loop():
    global BOT_PROCESS, BOT_SIGNATURE
    while not STOP_EVENT.is_set():
        try:
            config = load_config()
            telegram = config.get("telegram") or {}
            token = telegram.get("token", "")
            allowed = telegram.get("allowed_chat_ids", [])
            signature = hashlib.sha256(
                json.dumps([token, allowed], sort_keys=True).encode("utf-8")
            ).hexdigest() if token and allowed else None
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
    atexit.register(shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()
    threading.Thread(target=bot_supervisor_loop, name="telegram-bot", daemon=True).start()
    serve(app, host="0.0.0.0", port=8080, threads=8)


if __name__ == "__main__":
    main()
