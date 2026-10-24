import builtins
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

_open = builtins.open


def _open_test_config(path, *args, **kwargs):
    if str(path) == "/opt/m3u/config.json":
        return io.StringIO("{}")
    return _open(path, *args, **kwargs)


with patch("builtins.open", side_effect=_open_test_config):
    import recheck


class RecheckSourceSearchTests(unittest.TestCase):
    def setUp(self):
        self.original_config = recheck.cfg
        self.original_cookie = recheck._COOKIE
        self.original_sources = recheck._SEARCH_SOURCES
        recheck.cfg = {
            "decotv": {
                "url": "http://decotv:3000",
                "admin_username": "admin",
                "admin_password": "secret",
            },
            "cms_apis": ["https://selected.example/api"],
        }
        recheck._COOKIE = "auth=session"
        recheck._SEARCH_SOURCES = None

    def tearDown(self):
        recheck.cfg = self.original_config
        recheck._COOKIE = self.original_cookie
        recheck._SEARCH_SOURCES = self.original_sources

    def test_candidates_searches_enabled_decotv_sources_through_proxy(self):
        payloads = [
            {
                "Config": {
                    "SourceConfig": [
                        {"name": "Source One", "api": "https://one.example/api"},
                        {"name": "Source Two", "api": "https://two.example/api"},
                        {
                            "name": "Disabled",
                            "api": "https://disabled.example/api",
                            "disabled": True,
                        },
                    ]
                }
            },
            {
                "list": [{
                    "vod_name": "Example Series 第2季",
                    "vod_year": "2024",
                    "vod_play_url": (
                        "第1集$https://one.example/ep1.m3u8#"
                        "第2集$https://one.example/ep2.m3u8"
                    ),
                    "vod_play_from": "线路一",
                }]
            },
            {
                "list": [{
                    "vod_name": "Unrelated Series 第2季",
                    "vod_year": "2024",
                    "vod_play_url": "第1集$https://two.example/ep1.m3u8",
                    "vod_play_from": "线路二",
                }]
            },
        ]

        with patch.object(recheck, "http_json", side_effect=payloads) as mock_http_json:
            found = recheck.candidates("Example Series", "2024", season=2)

        self.assertEqual(
            found,
            [{
                "label": "Source One/线路一",
                "eps": [
                    ("第1集", "https://one.example/ep1.m3u8"),
                    ("第2集", "https://one.example/ep2.m3u8"),
                ],
            }],
        )
        self.assertEqual(mock_http_json.call_count, 3)
        self.assertEqual(
            mock_http_json.call_args_list[0].args,
            ("http://decotv:3000/api/admin/config", "auth=session"),
        )
        self.assertTrue(all(
            "/api/proxy/cms?url=" in call.args[0]
            for call in mock_http_json.call_args_list[1:]
        ))


if __name__ == "__main__":
    unittest.main()
