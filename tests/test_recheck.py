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

    def test_candidates_searches_enabled_decotv_sources_through_aggregate_search(self):
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
                "results": [{
                    "title": "Example Series",
                    "year": "2024",
                    "type_name": "剧集",
                    "remarks": "1080P",
                    "episodes": [
                        "https://one.example/ep1.m3u8",
                        "https://one.example/ep2.m3u8",
                    ],
                    "episodes_titles": ["第1集", "第2集"],
                    "source": "source-one",
                    "source_name": "Source One",
                }]
            },
        ]

        with patch.object(recheck, "http_json", side_effect=payloads) as mock_http_json:
            found = recheck.candidates("Example Series", "2024", season=1)

        self.assertEqual(
            found,
            [{
                "label": "Source One/Source One",
                "eps": [
                    ("第1集", "https://one.example/ep1.m3u8"),
                    ("第2集", "https://one.example/ep2.m3u8"),
                ],
            }],
        )
        self.assertEqual(mock_http_json.call_count, 2)
        self.assertEqual(
            mock_http_json.call_args_list[0].args,
            ("http://decotv:3000/api/admin/config", "auth=session"),
        )
        self.assertEqual(
            mock_http_json.call_args_list[1].args[0],
            "http://decotv:3000/api/search?q=Example+Series&adfilter=direct",
        )


if __name__ == "__main__":
    unittest.main()
