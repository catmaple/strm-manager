import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import decotv_search


class DecoTVSearchTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "decotv": {
                "url": "http://decotv:3000",
                "admin_username": "admin",
                "admin_password": "secret",
            },
            "cms_apis": ["https://selected.example/api"],
        }

    def test_get_sources_reads_all_enabled_non_adult_sources(self):
        http_json = Mock(return_value={
            "Config": {
                "SourceConfig": [
                    {"name": "One", "api": "https://one.example/api"},
                    {"key": "Two", "api": "https://two.example/api"},
                    {
                        "name": "Disabled",
                        "api": "https://disabled.example/api",
                        "disabled": True,
                    },
                    {
                        "name": "Adult",
                        "api": "https://adult.example/api",
                        "is_adult": True,
                    },
                    {"name": "Invalid", "api": "ftp://invalid.example/api"},
                ]
            }
        })

        sources = decotv_search.get_sources(
            self.config, "auth=session", http_json, lambda *_: None
        )

        self.assertEqual(
            sources,
            [
                {"name": "One", "api": "https://one.example/api"},
                {"name": "Two", "api": "https://two.example/api"},
            ],
        )
        http_json.assert_called_once_with(
            "http://decotv:3000/api/admin/config", "auth=session"
        )

    def test_search_all_uses_decotv_aggregate_search(self):
        http_json = Mock(return_value={
            "results": [
                {
                    "title": "Example Show",
                    "year": "2024",
                    "type_name": "剧集",
                    "remarks": "1080P",
                    "episodes": ["https://one.example/ep1.m3u8", "https://one.example/ep2.m3u8"],
                    "episodes_titles": ["第1集", "第2集"],
                    "source": "source-one",
                    "source_name": "Source One",
                }
            ]
        })

        results = decotv_search.search_all(
            self.config, "Example Show", [], "auth=session",
            http_json, lambda *_: None,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0]["name"], "Source One")
        self.assertEqual(results[0][1]["vod_name"], "Example Show")
        self.assertEqual(
            results[0][1]["vod_play_url"],
            "第1集$https://one.example/ep1.m3u8#第2集$https://one.example/ep2.m3u8",
        )
        http_json.assert_called_once_with(
            "http://decotv:3000/api/search?q=Example+Show&adfilter=direct",
            "auth=session",
        )

    def test_missing_admin_credentials_fall_back_to_cms_apis(self):
        config = {"cms_apis": ["https://selected.example/api"]}

        sources = decotv_search.get_sources(
            config, "", Mock(), lambda *_: None
        )

        self.assertEqual(
            sources,
            [{"name": "selected.example", "api": "https://selected.example/api"}],
        )


if __name__ == "__main__":
    unittest.main()
