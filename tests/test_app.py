import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as manager


class ScheduleDefaultsTests(unittest.TestCase):
    def test_fills_random_times_within_windows(self):
        seen = set()
        for _ in range(30):
            config = {}
            self.assertTrue(manager.fill_schedule_defaults(config))
            sched = config["scheduler"]
            self.assertTrue(1 <= sched["crawl"]["hour"] <= 3)
            self.assertTrue(5 <= sched["recheck"]["hour"] <= 6)
            self.assertTrue(8 <= sched["stats"]["hour"] <= 11)
            self.assertTrue(0 <= sched["stats"]["weekday"] <= 6)
            seen.add((sched["crawl"]["hour"], sched["crawl"]["minute"]))
        self.assertGreater(len(seen), 1)

    def test_keeps_existing_values(self):
        config = {"scheduler": {"crawl": {"hour": 2, "minute": 0, "script": "crawl"}}}
        manager.fill_schedule_defaults(config)
        self.assertEqual(config["scheduler"]["crawl"], {"hour": 2, "minute": 0, "script": "crawl"})
        snapshot = json.dumps(config, sort_keys=True)
        self.assertFalse(manager.fill_schedule_defaults(config))
        self.assertEqual(json.dumps(config, sort_keys=True), snapshot)


class DryRunTimeoutTests(unittest.TestCase):
    def test_timeout_returns_partial_output_and_restores_config(self):
        import subprocess

        class FakeProcess:
            returncode = None
            calls = 0
            killed = False

            def communicate(self, timeout=None):
                FakeProcess.calls += 1
                if FakeProcess.calls == 1:
                    raise subprocess.TimeoutExpired("crawl", timeout)
                return "line one\nline two\n", None

            def kill(self):
                FakeProcess.killed = True

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "config.json"
            target.write_text('{"original": true}', encoding="utf-8")
            real_path = manager.Path
            with patch.object(
                manager, "Path", side_effect=lambda value: target if value == "/opt/m3u/config.json" else real_path(value)
            ), patch.object(manager.subprocess, "Popen", return_value=FakeProcess()):
                message = manager.run_dry_run({"changed": True})
            self.assertIn("超时", message)
            self.assertIn("line two", message)
            self.assertTrue(FakeProcess.killed)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"original": True})


class ConfigWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.config_path = self.data_dir / "config.json"
        self.original_config_path = manager.CONFIG_PATH
        self.original_backup_dir = manager.BACKUP_DIR
        self.original_log_dir = manager.LOG_DIR
        manager.CONFIG_PATH = self.config_path
        manager.BACKUP_DIR = self.data_dir / "backups"
        manager.LOG_DIR = self.data_dir / "logs"
        manager.app.secret_key = "unit-test-session-secret"
        self.initial = {
            "cookie": "cookie-secret",
            "decotv": {
                "url": "http://decotv:3000",
                "admin_username": "admin",
                "admin_password": "decotv-admin-secret",
            },
            "login": {"username": "legacy-script-user", "password": "legacy-script-secret"},
            "jellyfin": {"url": "http://jellyfin:8096", "api_key": "jellyfin-secret"},
            "telegram": {"token": "telegram-secret", "allowed_chat_ids": [123]},
            "cms_apis": ["https://example.invalid/api"],
            "list_urls": ["https://example.invalid/list"],
            "unknown_option": {"keep": True},
        }
        self.config_path.write_text(
            json.dumps(self.initial), encoding="utf-8"
        )
        manager.app.config["TESTING"] = True
        self.client = manager.app.test_client()

    def tearDown(self):
        manager.CONFIG_PATH = self.original_config_path
        manager.BACKUP_DIR = self.original_backup_dir
        manager.LOG_DIR = self.original_log_dir
        self.temp.cleanup()

    def authenticate(self):
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["csrf_token"] = "test-csrf"

    def test_dashboard_requires_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_admin_login_sets_authenticated_session(self):
        with patch.object(manager, "ADMIN_USER", "admin"), patch.object(
            manager, "ADMIN_PASSWORD", "test-password-long"
        ):
            response = self.client.get("/login")
            csrf = re.search(
                r'name="csrf_token" value="([^"]+)"',
                response.get_data(as_text=True),
            ).group(1)
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": csrf,
                    "username": "admin",
                    "password": "test-password-long",
                },
            )
            self.assertEqual(response.status_code, 302)
            with patch.object(
                manager, "services_status", return_value={"DecoTV": "正常"}
            ):
                self.assertEqual(self.client.get("/").status_code, 200)

    @patch.object(manager, "services_status", return_value={"DecoTV": "正常", "Redis": "正常"})
    def test_dashboard_hides_credentials(self, _status):
        self.authenticate()
        response = self.client.get("/")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        for secret in (
            "cookie-secret",
            "decotv-admin-secret",
            "legacy-script-secret",
            "jellyfin-secret",
            "telegram-secret",
        ):
            self.assertNotIn(secret, body)
        self.assertIn("unknown_option", body)
        for field in (
            "decotv_admin_password",
            "jellyfin_api_key",
            "telegram_token",
        ):
            self.assertRegex(
                body,
                rf'<input id="{field}"[^>]*value="{re.escape(manager.SECRET_MASK)}"',
            )
        self.assertRegex(
            body,
            r'<input id="decotv_admin_username"[^>]*value="admin"[^>]*required',
        )
        self.assertIn("DecoTV 管理员密码", body)
        self.assertNotIn('id="cookie"', body)
        self.assertIn('id="config-view"', body)
        self.assertIn("unknown_option", body)
        self.assertIn("查看完整 config.json", body)
        self.assertIn('data-language-selector', body)
        self.assertIn("简体中文", body)
        self.assertIn("English", body)
        self.assertRegex(body, r'<script src="[^"]*i18n\.js"></script>')
        self.assertIn("扫描DecoTV默认分类", body)
        self.assertIn("扫描视频源分类", body)
        self.assertIn("Telegram 机器人提供的功能", body)
        self.assertIn("<code>/delete 片名</code>", body)
        self.assertIn("<code>/edit 片名</code>", body)
        self.assertIn("<code>/migrate</code>", body)
        for label in (
            "榜单抓取每次新增上限",
            "榜单抓取最大翻页数",
            "每次运行最多查询数",
            "未找到资源重试间隔（天）",
            "线路最低余量阈值",
            "切换线路所需提升倍数",
            "线路复测间隔（天）",
            "每次复测最大线路数",

            "跳过未标注年份的视频",
            "评分来源",
            "最低评分",
            "只抓取最近几年作品",
            "最低作品年份",
            "每次抓取新增上限",
            "每次抓取最大翻页数",
            "每个分类新增上限",
            "每个分类最大翻页数",
            "补库阶段最大页数",
            "增量更新最大页数",
            "剧集最大集数",
            "抓取请求间隔（秒）",
        ):
            self.assertIn(label, body)
        self.assertNotIn("crawl_via_decotv", body)
        self.assertIn('<option value="vod"', body)
        self.assertIn('<option value="any"', body)
        self.assertNotIn("自动整理电影分类", body)
        self.assertIn("线路复测", body)
        self.assertIn("统计数量", body)
        self.assertEqual(manager.hide_secrets(self.initial)["cookie"], manager.SECRET_MASK)
        self.assertIn("选择视频源（来自 DecoTV）", body)
        self.assertIn('id="decotvSourceSelect"', body)
        self.assertIn('id="sourceConfigWarning"', body)
        self.assertNotIn("sourceDebug", body)
        self.assertNotIn("调试请求信息", body)
        self.assertNotIn("请先到 DecoTV 设置并启用视频源", body)
        self.assertIn('id="saveConfigButton" type="submit"', body)
        self.assertNotIn('id="addCmsApiButton"', body)
        self.assertNotIn('id="clearCmsApiButton"', body)
        self.assertNotIn('id="decotvSourceList"', body)
        self.assertNotIn('id="cms_api_custom"', body)
        self.assertNotIn("cms-api-presets", body)
        self.assertNotIn("datalist", body)
        self.assertIn('id="cms_apis" name="cms_apis" type="hidden"', body)
        self.assertIn('id="crawl_sources" name="crawl_sources" type="hidden"', body)
        self.assertIn("忽略以下类型", body)
        self.assertIn("TC（枪版）", body)
        self.assertIn("TS（枪版）", body)
        self.assertIn("CAM（枪版）", body)
        self.assertNotIn("crawl.skip_remarks（JSON 数组）", body)
        self.assertNotIn('id="crawl_skip_remarks"', body)
        self.assertRegex(
            body,
            r'<input id="telegram_allowed_chat_ids"[^>]*value="123"',
        )
        self.assertNotIn('id="list_urls"', body)
        self.assertNotIn("自动生成 crawl.sources", body)
        self.assertNotIn("crawl.sources（JSON 数组）", body)

    def test_language_selector_is_available_on_login_page(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-language-selector', response.get_data(as_text=True))
        translations = self.client.get("/static/i18n.js")
        self.assertEqual(translations.status_code, 200)
        self.assertIn("STRM Manager Console", translations.get_data(as_text=True))
        translations.close()

    def test_skip_remark_checkboxes_save_config_values(self):
        self.authenticate()
        response = self.client.post(
            "/",
            data={
                "csrf_token": "test-csrf",
                "crawl_skip_remarks": ["TC", "CAM", "预告"],
            },
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved["crawl"]["skip_remarks"], ["TC", "CAM", "预告"])

    def test_chat_ids_use_comma_separated_numbers(self):
        self.assertEqual(manager.parse_form_chat_ids("123, -456"), [123, -456])
        with self.assertRaisesRegex(ValueError, "请输入数字"):
            manager.parse_form_chat_ids("[123]")

    def test_save_comma_separated_chat_ids_as_integer_list(self):
        self.authenticate()
        response = self.client.post(
            "/",
            data={
                "csrf_token": "test-csrf",
                "telegram_allowed_chat_ids": "123, -456",
            },
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved["telegram"]["allowed_chat_ids"], [123, -456])

    def test_save_decotv_admin_credentials(self):
        self.authenticate()
        response = self.client.post(
            "/",
            data={
                "csrf_token": "test-csrf",
                "decotv_url": "http://decotv:3000",
                "decotv_admin_username": "deco-admin",
                "decotv_admin_password": "new-admin-secret",
            },
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved["decotv"]["admin_username"], "deco-admin")
        self.assertEqual(saved["decotv"]["admin_password"], "new-admin-secret")

    @patch.object(
        manager,
        "discover_sources_from_cms_apis",
        return_value=[{
            "name": "New Source",
            "api": "https://new-source.example/api",
            "types": {"1": "movie"},
        }],
    )
    def test_save_changed_cms_api_refreshes_crawl_sources(self, mock_discover):
        self.authenticate()
        current = {
            **self.initial,
            "cms_apis": ["https://old-source.example/api"],
            "crawl": {
                "sources": [{
                    "name": "Old Source",
                    "api": "https://old-source.example/api",
                    "types": {"1": "tv"},
                }],
            },
        }
        self.config_path.write_text(json.dumps(current), encoding="utf-8")
        response = self.client.post(
            "/",
            data={
                "csrf_token": "test-csrf",
                "cms_apis": '["https://new-source.example/api"]',
                "crawl_sources": json.dumps(current["crawl"]["sources"]),
            },
        )

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved["cms_apis"], ["https://new-source.example/api"])
        self.assertEqual(
            saved["crawl"]["sources"],
            [{
                "name": "New Source",
                "api": "https://new-source.example/api",
                "types": {"1": "movie"},
            }],
        )
        mock_discover.assert_called_once_with(["https://new-source.example/api"])

    @patch.object(manager, "discover_sources_from_cms_apis", return_value=[])
    def test_save_changed_cms_api_without_categories_is_rejected(self, _mock_discover):
        self.authenticate()
        current = {
            **self.initial,
            "cms_apis": ["https://old-source.example/api"],
            "crawl": {
                "sources": [{
                    "name": "Old Source",
                    "api": "https://old-source.example/api",
                    "types": {"1": "tv"},
                }],
            },
        }
        original = json.dumps(current)
        self.config_path.write_text(original, encoding="utf-8")
        response = self.client.post(
            "/",
            data={
                "csrf_token": "test-csrf",
                "cms_apis": '["https://new-source.example/api"]',
                "crawl_sources": json.dumps(current["crawl"]["sources"]),
            },
        )

        saved = self.config_path.read_text(encoding="utf-8")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved, original)

    def test_hidden_list_urls_are_preserved_by_form_submission(self):
        submitted = manager.config_from_form({})
        merged = manager.merge_config(self.initial, submitted)
        self.assertEqual(merged["list_urls"], self.initial["list_urls"])

    def test_form_masks_keep_existing_secrets(self):
        parsed = manager.config_from_form({
            "decotv_admin_password": manager.SECRET_MASK,
            "jellyfin_api_key": manager.SECRET_MASK,
            "telegram_token": manager.SECRET_MASK,
        })
        self.assertEqual(parsed["decotv"]["admin_password"], "")
        self.assertEqual(parsed["jellyfin"]["api_key"], "")
        self.assertEqual(parsed["telegram"]["token"], "")

    @patch.object(manager, "test_service_connections", return_value="DecoTV：正常\nJellyfin：正常")
    def test_test_connection_action(self, mock_test):
        self.authenticate()
        response = self.client.post(
            "/",
            data={"csrf_token": "test-csrf", "action": "test_connection"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(mock_test.called)

    @patch.object(manager, "run_dry_run", return_value="Dry Run 完成（退出码 0）\n模拟抓取 0 项")
    def test_dry_run_action(self, mock_dry_run):
        self.authenticate()
        response = self.client.post(
            "/",
            data={"csrf_token": "test-csrf", "action": "dry_run"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(mock_dry_run.called)

    @patch.object(manager, "run_dry_run", return_value="Dry Run 完成（退出码 0）\n模拟抓取 0 项")
    def test_last_action_result_is_displayed(self, _mock_dry_run):
        self.authenticate()
        self.client.post(
            "/",
            data={"csrf_token": "test-csrf", "action": "dry_run"},
        )
        response = self.client.get("/")
        body = response.get_data(as_text=True)
        self.assertIn("最近操作结果", body)
        self.assertIn("Dry Run 完成（退出码 0）", body)

    @patch.object(manager.urllib.request, "urlopen")
    def test_discover_sources_from_cms_apis(self, mock_urlopen):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def read(self):
                return json.dumps({"class": [{"type_id": "1", "type_name": "电影"}, {"type_id": "2", "type_name": "连续剧"}]}).encode("utf-8")
            def __iter__(self):
                return iter([json.dumps({"class": [{"type_id": "1", "type_name": "电影"}, {"type_id": "2", "type_name": "连续剧"}]})])
        mock_urlopen.return_value = FakeResponse()
        sources = manager.discover_sources_from_cms_apis(["https://hhzyapi.com/api.php/provide/vod"])
        self.assertEqual(sources[0]["types"]["1"], "movie")
        self.assertEqual(sources[0]["types"]["2"], "tv")

    @patch.object(manager, "discover_sources_from_cms_apis", return_value=[{"name": "example.com", "api": "https://example.com", "types": {"1": "movie"}}])
    def test_generate_sources_action(self, mock_generate):
        self.authenticate()
        response = self.client.post(
            "/",
            data={
                "csrf_token": "test-csrf",
                "action": "generate_sources",
                "cms_apis": '["https://example.com/api.php/provide/vod"]',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(mock_generate.called)

    @patch.object(
        manager,
        "discover_sources_from_cms_apis",
        return_value=[{"name": "example.com", "api": "https://example.com", "types": {"1": "movie"}}],
    )
    def test_generate_sources_api_returns_sources_for_form(self, mock_generate):
        self.authenticate()
        response = self.client.post(
            "/api/generate_sources",
            data={
                "csrf_token": "test-csrf",
                "cms_apis": '["https://example.com/api.php/provide/vod"]',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["count"], 1)
        self.assertEqual(response.json["sources"][0]["types"]["1"], "movie")
        mock_generate.assert_called_once_with(["https://example.com/api.php/provide/vod"])

    @patch.object(manager.urllib.request, "urlopen")
    def test_decotv_sources_api_lists_enabled_sources_without_other_config(self, mock_urlopen):
        self.authenticate()

        class FakeHeaders:
            def get_all(self, name):
                return ["auth=session-cookie; Path=/; HttpOnly"] if name == "Set-Cookie" else []

        class LoginResponse:
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        login_response = LoginResponse()

        class ConfigResponse(LoginResponse):
            headers = FakeHeaders()

            def read(self, _limit):
                return json.dumps({
                    "Role": "owner",
                    "Config": {
                        "SourceConfig": [
                            {"name": "Source One", "api": "https://source.example/api", "disabled": False},
                            {"key": "Disabled", "api": "https://disabled.example/api", "disabled": True},
                            {"name": "Adult", "api": "https://adult.example/api", "is_adult": True},
                            {"name": "Invalid", "api": "file:///etc/passwd"},
                            {"name": "Duplicate", "api": "https://source.example/api"},
                        ],
                        "UserConfig": {"Users": [{"username": "private-user"}]},
                    },
                }).encode("utf-8")

        mock_urlopen.side_effect = [login_response, ConfigResponse()]
        response = self.client.get("/api/decotv/sources")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["sources"],
            [{"name": "Source One", "api": "https://source.example/api"}],
        )
        self.assertNotIn("UserConfig", response.get_data(as_text=True))
        self.assertEqual(
            mock_urlopen.call_args_list[0].args[0].full_url,
            "http://decotv:3000/api/login",
        )
        self.assertEqual(mock_urlopen.call_args_list[0].args[0].get_method(), "POST")
        self.assertEqual(
            json.loads(mock_urlopen.call_args_list[0].args[0].data),
            {"username": "admin", "password": "decotv-admin-secret"},
        )
        self.assertEqual(
            mock_urlopen.call_args_list[1].args[0].full_url,
            "http://decotv:3000/api/admin/config",
        )
        self.assertEqual(
            mock_urlopen.call_args_list[1].args[0].get_header("Cookie"),
            "auth=session-cookie",
        )

    @patch.object(manager.urllib.request, "urlopen")
    def test_decotv_sources_api_prompts_when_no_sources_are_configured(self, mock_urlopen):
        self.authenticate()

        class FakeHeaders:
            def get_all(self, name):
                return ["auth=session-cookie; Path=/; HttpOnly"] if name == "Set-Cookie" else []

        class FakeResponse:
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"Config":{"SourceConfig":[]}}'

        mock_urlopen.side_effect = [FakeResponse(), FakeResponse()]
        response = self.client.get("/api/decotv/sources")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["sources"], [])
        self.assertIn("DecoTV 管理配置中尚未添加视频源", response.json["message"])

    @patch.object(
        manager,
        "load_config",
        return_value={
            "decotv": {
                "url": "http://decotv:3000",
                "admin_username": "admin",
                "admin_password": "",
            },
        },
    )
    def test_decotv_sources_api_prompts_for_missing_admin_credentials(self, _load_config):
        self.authenticate()

        response = self.client.get("/api/decotv/sources")

        self.assertEqual(response.status_code, 502)
        self.assertIn("尚未配置 DecoTV 管理员凭证", response.json["message"])
        self.assertIn("点击“校验并保存”后刷新页面", response.json["message"])

    @patch.object(
        manager,
        "fetch_decotv_sources",
        return_value=[],
    )
    def test_decotv_sources_api_explains_no_available_sources(self, _fetch_sources):
        self.authenticate()

        response = self.client.get("/api/decotv/sources")

        self.assertEqual(response.status_code, 502)
        self.assertIn("没有可用的已启用非成人视频源", response.json["message"])
        self.assertIn("禁用源和成人源不会显示", response.json["message"])

    def test_decotv_sources_api_requires_authentication(self):
        response = self.client.get("/api/decotv/sources")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    @patch.object(
        manager.urllib.request,
        "urlopen",
        side_effect=manager.urllib.error.HTTPError(
            "http://decotv:3000/api/admin/config", 401, "Unauthorized", {}, None
        ),
    )
    def test_decotv_sources_api_explains_upstream_auth_failure(self, _mock_urlopen):
        self.authenticate()

        response = self.client.get("/api/decotv/sources")

        self.assertEqual(response.status_code, 502)
        self.assertIn("DecoTV 管理员登录失败：HTTP 401", response.json["message"])
        self.assertIn("管理员账号和密码", response.json["message"])

    def test_fetch_decotv_sources_requires_password_in_config(self):
        with self.assertRaisesRegex(ValueError, "尚未配置 DecoTV 管理员凭证"):
            manager.fetch_decotv_sources({
                "decotv": {"url": "http://decotv:3000"},
                "login": {"username": "admin", "password": "script-password"},
            })

    def test_admin_password_is_hidden_in_config_view(self):
        hidden = manager.hide_secrets({
            "decotv": {"admin_username": "admin", "admin_password": "secret"},
        })
        self.assertEqual(hidden["decotv"]["admin_password"], manager.SECRET_MASK)
        self.assertEqual(hidden["decotv"]["admin_username"], "admin")

    def test_telegram_bot_signature_changes_when_search_config_changes(self):
        original = {
            "telegram": {"token": "token", "allowed_chat_ids": [123]},
            "cms_apis": ["https://one.example/api"],
            "decotv": {"url": "http://decotv:3000", "admin_username": "admin"},
            "jellyfin": {"url": "http://jellyfin:8096"},
        }
        changed_sources = {
            **original,
            "cms_apis": ["https://two.example/api"],
        }
        changed_credentials = {
            **original,
            "decotv": {
                **original["decotv"],
                "admin_password": "changed",
            },
        }

        original_signature = manager.telegram_bot_signature(original)

        self.assertEqual(manager.telegram_bot_signature(original), original_signature)
        self.assertNotEqual(manager.telegram_bot_signature(changed_sources), original_signature)
        self.assertNotEqual(
            manager.telegram_bot_signature(changed_credentials),
            original_signature,
        )

    def test_scheduler_form_parses_schedule_settings(self):
        parsed = manager.config_from_form({
            "scheduler_enabled": "on",
            "scheduler_crawl_enabled": "on",
            "scheduler_crawl_script": "crawl",
            "scheduler_crawl_hour": "02",
            "scheduler_crawl_minute": "15",
            "scheduler_recheck_enabled": "off",
            "scheduler_recheck_hour": "04",
            "scheduler_recheck_minute": "30",
            "scheduler_stats_enabled": "on",
            "scheduler_stats_weekday": "6",
            "scheduler_stats_hour": "09",
            "scheduler_stats_minute": "00",
        })
        self.assertTrue(parsed["scheduler"]["enabled"])
        self.assertEqual(parsed["scheduler"]["crawl"]["script"], "crawl")
        self.assertEqual(parsed["scheduler"]["crawl"]["hour"], 2)
        self.assertEqual(parsed["scheduler"]["crawl"]["minute"], 15)
        self.assertEqual(parsed["scheduler"]["stats"]["weekday"], 6)

    @patch.object(manager, "run_manual_job", return_value="已启动任务：指定源抓取")
    def test_run_manual_job_action(self, mock_run_manual_job):
        self.authenticate()
        response = self.client.post(
            "/",
            data={"csrf_token": "test-csrf", "action": "run_job_crawl"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(mock_run_manual_job.called)

    @patch.object(manager, "run_manual_job", return_value="已启动任务：默认分类抓取")
    def test_run_manual_auto_job_action(self, mock_run_manual_job):
        self.authenticate()
        response = self.client.post(
            "/",
            data={"csrf_token": "test-csrf", "action": "run_job_auto"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(mock_run_manual_job.called)

    @patch.object(manager, "write_log")
    @patch.object(
        manager.subprocess,
        "run",
        side_effect=[
            manager.subprocess.CompletedProcess(["auto.py"], 0, "crawl ok"),
            manager.subprocess.CompletedProcess(["reclassify_media.py"], 0, "reclassify ok"),
        ],
    )
    def test_scheduled_crawl_runs_reclassification_after_success(self, mock_run, _write_log):
        with patch.dict(manager.RUNTIME, {"job": None, "last_job": "previous"}):
            manager.scheduled_job(
                "默认分类抓取", "auto.py", 10, reclassify_after=True
            )
            self.assertEqual(mock_run.call_count, 2)
            self.assertEqual(
                mock_run.call_args_list[1].args[0][-1],
                "/opt/m3u/reclassify_media.py",
            )
            self.assertIn("电影分类整理完成", manager.RUNTIME["last_job"])

    @patch.object(manager, "write_log")
    @patch.object(
        manager.subprocess,
        "run",
        return_value=manager.subprocess.CompletedProcess(["crawl.py"], 1, "crawl failed"),
    )
    def test_failed_scheduled_crawl_skips_reclassification(self, mock_run, _write_log):
        with patch.dict(manager.RUNTIME, {"job": None, "last_job": "previous"}):
            manager.scheduled_job(
                "指定源抓取", "crawl.py", 10, reclassify_after=True
            )
            mock_run.assert_called_once()
            self.assertIn("失败", manager.RUNTIME["last_job"])

    @patch.object(manager, "write_log")
    @patch.object(
        manager.subprocess,
        "run",
        return_value=manager.subprocess.CompletedProcess(["x.py"], 0, "ok"),
    )
    def test_busy_manual_job_is_skipped_but_scheduled_job_waits_its_turn(self, mock_run, _write_log):
        import threading
        import time

        manager.JOB_LOCK.acquire()
        try:
            manager.scheduled_job("手动任务", "x.py", 10)
            mock_run.assert_not_called()

            worker = threading.Thread(
                target=manager.scheduled_job, args=("定时任务", "x.py", 10, False, True)
            )
            worker.start()
            time.sleep(0.3)
            mock_run.assert_not_called()
        finally:
            manager.JOB_LOCK.release()
        worker.join(timeout=15)
        self.assertFalse(worker.is_alive())
        mock_run.assert_called_once()

    @patch.object(manager, "scheduled_job")
    def test_run_due_jobs_runs_every_job_in_order_with_waiting(self, mock_job):
        jobs = [("A", "a.py", 1, True), ("B", "b.py", 2, False)]
        manager.run_due_jobs(jobs)
        self.assertEqual(
            [call.args[0] for call in mock_job.call_args_list], ["A", "B"]
        )
        self.assertTrue(all(call.kwargs == {"wait": True} for call in mock_job.call_args_list))

    def test_scheduler_batches_jobs_due_in_the_same_minute(self):
        from datetime import datetime as real_datetime

        config = {"scheduler": {
            "crawl": {"script": "auto", "hour": 4, "minute": 30},
            "recheck": {"hour": 4, "minute": 30},
        }}
        started = []

        class FakeThread:
            def __init__(self, target=None, args=(), **_kwargs):
                started.append((target, args))

            def start(self):
                pass

        with patch.object(manager, "load_config", return_value=config), patch.object(
            manager, "datetime"
        ) as mock_datetime, patch.object(manager.threading, "Thread", FakeThread), patch.object(
            manager.STOP_EVENT, "wait", side_effect=[False, False, True]
        ):
            mock_datetime.now.return_value = real_datetime(2026, 10, 26, 4, 30, 5)
            manager.scheduler_loop()

        # 两个任务同一分钟到点：只启动一次批处理，且连续两轮检测不会重复启动
        self.assertEqual(len(started), 1)
        target, args = started[0]
        self.assertIs(target, manager.run_due_jobs)
        self.assertEqual([job[0] for job in args[0]], ["默认分类抓取", "线路复测"])


    def test_save_preserves_blank_secrets_and_unknown_fields(self):
        self.authenticate()
        submitted = {
            **self.initial,
            "cookie": "",
            "jellyfin": {"url": "http://jellyfin:8096", "api_key": ""},
            "telegram": {"token": "", "allowed_chat_ids": [123]},
            "max_new_per_run": 45,
        }
        response = self.client.post(
            "/",
            data={"csrf_token": "test-csrf", "config_json": json.dumps(submitted)},
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved["cookie"], "cookie-secret")
        self.assertEqual(saved["decotv"]["admin_password"], "decotv-admin-secret")
        self.assertNotIn("login", saved)
        self.assertEqual(saved["jellyfin"]["api_key"], "jellyfin-secret")
        self.assertEqual(saved["telegram"]["token"], "telegram-secret")
        self.assertEqual(saved["max_new_per_run"], 45)
        self.assertEqual(saved["unknown_option"], {"keep": True})
        backups = list((self.data_dir / "backups").glob("config-*.json"))
        self.assertEqual(len(backups), 1)
        if os.name == "posix":
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_invalid_config_is_not_saved(self):
        self.authenticate()
        original = self.config_path.read_text(encoding="utf-8")
        response = self.client.post(
            "/",
            data={
                "csrf_token": "test-csrf",
                "config_json": '{"crawl":{"via_decotv":"yes"}}',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), original)

    def test_post_requires_csrf(self):
        self.authenticate()
        response = self.client.post("/", data={"config_json": "{}"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
