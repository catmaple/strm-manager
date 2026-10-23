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
