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

    def test_search_all_queries_each_source_through_decotv_proxy(self):
        sources = [
            {"name": "One", "api": "https://one.example/api"},
            {"name": "Two", "api": "https://two.example/api"},
        ]
        http_json = Mock(side_effect=[
            {"list": [{"vod_name": "Example"}]},
            {"list": [{"vod_name": "Example"}]},
        ])

        results = decotv_search.search_all(
            self.config, "Example Show", sources, "auth=session",
            http_json, lambda *_: None,
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(http_json.call_count, 2)
        self.assertTrue(all(call.args[1] == "auth=session" for call in http_json.call_args_list))
        self.assertTrue(all(
            "/api/proxy/cms?url=" in call.args[0]
            for call in http_json.call_args_list
        ))

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
