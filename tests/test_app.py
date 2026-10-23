import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as manager


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
            "login": {"username": "script", "password": "login-secret"},
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
        for secret in ("cookie-secret", "login-secret", "jellyfin-secret", "telegram-secret"):
            self.assertNotIn(secret, body)
        self.assertIn("unknown_option", body)
        for field in ("login_password", "jellyfin_api_key", "telegram_token"):
            self.assertRegex(
                body,
                rf'<input id="{field}"[^>]*value="{re.escape(manager.SECRET_MASK)}"',
            )
        self.assertNotIn('id="cookie"', body)
        self.assertIn('id="config-view"', body)
        self.assertIn("unknown_option", body)
        self.assertIn("查看完整 config.json", body)
        self.assertIn('data-language-selector', body)
        self.assertIn("简体中文", body)
        self.assertIn("English", body)
        self.assertIn("扫描DecoTV默认分类", body)
        self.assertIn("扫描视频源分类", body)
        self.assertNotIn("自动整理电影分类", body)
        self.assertIn("线路复测", body)
        self.assertIn("统计数量", body)
        self.assertEqual(manager.hide_secrets(self.initial)["cookie"], manager.SECRET_MASK)
        self.assertIn("选择视频源或添加", body)
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

    def test_hidden_list_urls_are_preserved_by_form_submission(self):
        submitted = manager.config_from_form({})
        merged = manager.merge_config(self.initial, submitted)
        self.assertEqual(merged["list_urls"], self.initial["list_urls"])

    def test_form_masks_keep_existing_secrets(self):
        parsed = manager.config_from_form({
            "cookie": manager.SECRET_MASK,
            "login_password": manager.SECRET_MASK,
            "jellyfin_api_key": manager.SECRET_MASK,
            "telegram_token": manager.SECRET_MASK,
        })
        self.assertNotIn("cookie", parsed)
        self.assertEqual(parsed["login"]["password"], "")
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

    def test_cms_api_presets_include_common_providers(self):
        self.assertIn("https://hhzyapi.com/api.php/provide/vod", manager.CMS_API_PRESETS)
        self.assertIn("https://api.xinlangapi.com/xinlangapi.php/provide/vod/", manager.CMS_API_PRESETS)

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


    def test_save_preserves_blank_secrets_and_unknown_fields(self):
        self.authenticate()
        submitted = {
            **self.initial,
            "cookie": "",
            "login": {"username": "script", "password": ""},
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
        self.assertEqual(saved["login"]["password"], "login-secret")
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
