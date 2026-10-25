import builtins
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import add
import decotv_search
import seasons

_open = builtins.open


def _open_test_config(path, *args, **kwargs):
    if str(path) == "/opt/m3u/config.json":
        return io.StringIO("{}")
    return _open(path, *args, **kwargs)


class AddSearchTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "decotv": {
                "url": "http://decotv:3000",
                "admin_username": "admin",
                "admin_password": "secret",
            },
            "cms_apis": ["https://selected.example/api.php/provide/vod"],
        }

    def test_search_uses_decotv_aggregate_search_when_admin_is_configured(self):
        with patch.object(decotv_search, "get_cookie", return_value="auth=1"), patch.object(
            decotv_search, "get_sources", return_value=[{"name": "One", "api": "https://one.example"}]
        ), patch.object(add, "http_json", return_value={"results": [{
            "title": "示例电影", "year": "2024", "type_name": "电影",
            "episodes": ["https://one.example/m.m3u8"], "episodes_titles": ["正片"],
            "source": "one", "source_name": "百度资源",
        }]}) as mock_http:
            records = add.search_records(self.cfg, "示例电影")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0]["name"], "百度资源")
        self.assertTrue(mock_http.call_args.args[0].startswith("http://decotv:3000/api/search?"))
        self.assertEqual(mock_http.call_args.args[1], "auth=1")

    def test_search_falls_back_to_direct_cms_without_admin_credentials(self):
        cfg = {"cms_apis": ["https://selected.example/api.php/provide/vod"]}
        with patch.object(add, "http_json", return_value={"list": [{"vod_name": "示例电影"}]}) as mock_http:
            records = add.search_records(cfg, "示例电影")

        self.assertEqual([record["vod_name"] for _source, record in records], ["示例电影"])
        self.assertEqual(records[0][0]["name"], "selected.example")
        self.assertIn("selected.example/api.php/provide/vod?ac=detail&wd=", mock_http.call_args.args[0])

    def test_main_writes_strm_from_aggregate_results_with_source_name_label(self):
        record = {
            "vod_name": "示例电影", "vod_year": "2024", "type_name": "电影",
            "vod_play_url": "正片$https://one.example/m.m3u8", "vod_play_from": "百度资源",
        }
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            seasons, "MOVIE_ROOT", str(Path(temporary) / "movies")
        ), patch.object(add, "search_records", return_value=[({"name": "百度资源", "api": "x"}, record)]), patch.object(
            sys, "argv", ["add.py", "示例电影"]
        ), patch("builtins.open", side_effect=_open_test_config), patch("builtins.print") as mock_print:
            add.main()

            strm = Path(temporary) / "movies" / "示例电影 (2024)" / "示例电影 (2024).strm"
            self.assertEqual(strm.read_text(encoding="utf-8"), "https://one.example/m.m3u8\n")
            output = " ".join(str(arg) for call in mock_print.call_args_list for arg in call.args)
            self.assertIn("线路 百度资源", output)
            self.assertNotIn("百度资源/百度资源", output)


class DecoTVLoginTests(unittest.TestCase):
    def test_get_cookie_without_credentials_uses_config_cookie(self):
        self.assertEqual(decotv_search.get_cookie({"cookie": "legacy=1"}), "legacy=1")

    def test_get_cookie_logs_in_and_joins_set_cookie_values(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get_all.return_value = ["auth=abc; Path=/; HttpOnly", "sid=1; Path=/"]
        cfg = {"decotv": {"admin_username": " admin ", "admin_password": "pw"}}
        with patch.object(decotv_search.urllib.request, "urlopen", return_value=response) as mock_open:
            cookie = decotv_search.get_cookie(cfg, lambda *_: None)

        self.assertEqual(cookie, "auth=abc; sid=1")
        request = mock_open.call_args.args[0]
        self.assertEqual(request.full_url, "http://decotv:3000/api/login")
        self.assertIn(b'"username": "admin"', request.data)

    def test_get_cookie_falls_back_when_login_fails(self):
        cfg = {"cookie": "legacy=1", "decotv": {"admin_username": "admin", "admin_password": "pw"}}
        with patch.object(decotv_search.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertEqual(decotv_search.get_cookie(cfg, lambda *_: None), "legacy=1")


if __name__ == "__main__":
    unittest.main()
