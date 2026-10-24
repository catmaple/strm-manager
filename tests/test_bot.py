import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bot as telegram_bot


class TelegramSearchTests(unittest.TestCase):
    def setUp(self):
        self.original_config = telegram_bot.cfg
        telegram_bot.cfg = {
            "decotv": {
                "url": "http://decotv:3000",
                "admin_username": "admin",
                "admin_password": "admin-secret",
            },
            "cms_apis": ["https://cms.example/api.php/provide/vod"],
        }

    def tearDown(self):
        telegram_bot.cfg = self.original_config

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_search_uses_decotv_proxy_and_admin_session(self, mock_http_json, _cookie):
        mock_http_json.return_value = {
            "list": [{
                "vod_name": "Example Movie",
                "vod_year": "2024",
                "vod_play_url": "线路一$https://media.example/movie.m3u8",
                "vod_play_from": "线路一",
            }],
        }

        results = telegram_bot.candidates("Example Movie")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["full"], "Example Movie")
        request_url, cookie = mock_http_json.call_args.args
        self.assertEqual(cookie, "auth=session-cookie")
        parsed_proxy_url = urllib.parse.urlsplit(request_url)
        self.assertEqual(parsed_proxy_url.path, "/api/proxy/cms")
        target_url = urllib.parse.parse_qs(parsed_proxy_url.query)["url"][0]
        target_query = urllib.parse.parse_qs(urllib.parse.urlsplit(target_url).query)
        self.assertEqual(target_query["ac"], ["detail"])
        self.assertEqual(target_query["wd"], ["Example Movie"])

    @patch.object(telegram_bot.urllib.request, "urlopen")
    def test_decotv_cookie_login_uses_admin_credentials(self, mock_urlopen):
        class FakeHeaders:
            def get_all(self, name):
                return ["auth=session-cookie; Path=/; HttpOnly"] if name == "Set-Cookie" else []

        class FakeResponse:
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        mock_urlopen.return_value = FakeResponse()

        cookie = telegram_bot.get_decotv_cookie()

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(cookie, "auth=session-cookie")
        self.assertEqual(request.full_url, "http://decotv:3000/api/login")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            json.loads(request.data),
            {"username": "admin", "password": "admin-secret"},
        )


if __name__ == "__main__":
    unittest.main()
