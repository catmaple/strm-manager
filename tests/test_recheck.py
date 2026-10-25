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
    def test_covers_local_requires_every_local_episode_number(self):
        item = {"kind": "tv", "files": ["S S01E01.strm", "S S01E03.strm"]}
        gap = [("第1集", "u1"), ("第2集", "u2")]
        full = [("第1集", "u1"), ("第2集", "u2"), ("第3集", "u3")]
        self.assertFalse(recheck.covers_local(item, gap))
        self.assertTrue(recheck.covers_local(item, full))
        self.assertTrue(recheck.covers_local({"kind": "movie", "files": ["m.strm"]}, [("正片", "u")]))

    def test_write_back_uses_episode_numbers_and_atomic_files(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temporary:
            item = {"kind": "tv", "season": 1, "dir": temporary, "files": []}
            recheck.write_back(item, "示例", [("第1集", "https://n/1.m3u8"), ("第3集", "https://n/3.m3u8")])
            self.assertEqual(
                sorted(path.name for path in Path(temporary).iterdir()),
                ["示例 S01E01.strm", "示例 S01E03.strm"],
            )

    def test_candidates_excludes_series_when_local_item_is_movie(self):
        payloads = [
            {"Config": {"SourceConfig": [{"name": "One", "api": "https://one.example/api"}]}},
            {"results": [
                {
                    "title": "Example", "year": "2024", "type_name": "连续剧",
                    "episodes": ["https://one.example/1.m3u8", "https://one.example/2.m3u8", "https://one.example/3.m3u8"],
                    "episodes_titles": ["第1集", "第2集", "第3集"],
                    "source": "one", "source_name": "One",
                },
                {
                    "title": "Example", "year": "2024", "type_name": "电影",
                    "episodes": ["https://one.example/movie.m3u8"],
                    "episodes_titles": ["正片"],
                    "source": "two", "source_name": "Two",
                },
            ]},
        ]
        with patch.object(recheck, "http_json", side_effect=payloads):
            found = recheck.candidates("Example", "2024", season=1, kind="movie")

        self.assertEqual(
            [item["eps"] for item in found],
            [[("正片", "https://one.example/movie.m3u8")]],
        )


if __name__ == "__main__":
    unittest.main()
