import json
import subprocess
import sys
import tempfile
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
            "decotv": {"url": "http://decotv:3000"},
            "cms_apis": ["https://cms.example/api.php/provide/vod"],
        }

    def tearDown(self):
        telegram_bot.cfg = self.original_config

    def source_config(self, sources=None):
        return {
            "Config": {
                "SourceConfig": sources or [{
                    "name": "Example CMS",
                    "api": "https://cms.example/api.php/provide/vod",
                }]
            }
        }

    def set_search_responses(self, mock_http_json, *responses):
        mock_http_json.side_effect = responses

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_search_uses_decotv_proxy_and_admin_session(self, mock_http_json, _cookie):
        self.set_search_responses(mock_http_json, {
            "list": [{
                "vod_name": "Example Movie",
                "vod_year": "2024",
                "vod_play_url": "线路一$https://media.example/movie.m3u8",
                "vod_play_from": "线路一",
            }],
        })

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

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_search_removes_episode_suffix_from_query(
        self, mock_http_json, _cookie
    ):
        self.set_search_responses(mock_http_json, {"list": []})

        telegram_bot.candidates("Example Series 第2季 第3集")

        request_url = mock_http_json.call_args.args[0]
        target_url = urllib.parse.parse_qs(
            urllib.parse.urlsplit(request_url).query
        )["url"][0]
        target_query = urllib.parse.parse_qs(urllib.parse.urlsplit(target_url).query)
        self.assertEqual(target_query["wd"], ["Example Series"])

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_search_uses_all_enabled_non_adult_decotv_sources(
        self, mock_http_json, _cookie
    ):
        telegram_bot.cfg["decotv"].update({
            "admin_username": "admin",
            "admin_password": "admin-secret",
        })
        mock_http_json.return_value = {"results": [
            {
                "id": "movie-1",
                "source": "source-one",
                "source_name": "Source One",
                "title": "Example Movie",
                "year": "2024",
                "type_name": "电影",
                "episodes": ["https://one.example/movie.m3u8"],
                "episodes_titles": ["播放"],
            },
            {
                "id": "movie-1",
                "source": "source-one",
                "source_name": "Source One",
                "title": "Example Movie",
                "year": "2024",
                "type_name": "电影",
                "episodes": ["https://one.example/movie.m3u8"],
                "episodes_titles": ["播放"],
            },
            {
                "id": "movie-2",
                "source": "source-two",
                "source_name": "Source Two",
                "title": "Example Movie",
                "year": "2024",
                "type_name": "电影",
                "episodes": ["https://two.example/movie.m3u8"],
                "episodes_titles": ["播放"],
            },
        ]}

        results = telegram_bot.candidates("Example Movie")

        self.assertEqual(len(results), 2)
        self.assertEqual(
            [result["lines"][0]["label"] for result in results],
            ["Source One", "Source Two"],
        )
        request_url, cookie = mock_http_json.call_args.args
        self.assertEqual(cookie, "auth=session-cookie")
        parsed_url = urllib.parse.urlsplit(request_url)
        self.assertEqual(parsed_url.path, "/api/search")
        self.assertEqual(
            urllib.parse.parse_qs(parsed_url.query),
            {"q": ["Example Movie"], "adfilter": ["direct"]},
        )

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_decotv_search_results_keep_page_order_and_filter_non_hls(
        self, mock_http_json, _cookie
    ):
        telegram_bot.cfg["decotv"].update({
            "admin_username": "admin",
            "admin_password": "admin-secret",
        })
        mock_http_json.return_value = {"results": [
            {
                "id": "first",
                "source": "source-one",
                "source_name": "Source One",
                "title": "Example Series 第2季",
                "year": "2024",
                "type_name": "剧集",
                "episodes": [
                    "https://one.example/ep1.m3u8",
                    "https://one.example/ep2.mp4",
                ],
                "episodes_titles": ["第1集", "第2集"],
            },
            {
                "id": "second",
                "source": "source-two",
                "source_name": "Source Two",
                "title": "Example Series 第2季",
                "year": "2024",
                "type_name": "剧集",
                "episodes": ["https://two.example/ep1.m3u8"],
                "episodes_titles": ["第1集"],
            },
        ]}

        results = telegram_bot.candidates("Example Series 第2季")

        self.assertEqual(
            [result["lines"][0]["label"] for result in results],
            ["Source One", "Source Two"],
        )
        self.assertEqual(
            results[0]["eps"],
            [("第1集", "https://one.example/ep1.m3u8")],
        )
        self.assertEqual(results[0]["season"], 2)
        self.assertTrue(all(result["kind"] == "tv" for result in results))

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_search_recognizes_unknown_category_with_multiple_episodes(
        self, mock_http_json, _cookie
    ):
        self.set_search_responses(mock_http_json, {
            "list": [{
                "vod_name": "Example Series",
                "vod_year": "2024",
                "type_name": "国产",
                "vod_play_url": (
                    "第1集$https://media.example/ep1.m3u8#"
                    "第2集$https://media.example/ep2.m3u8"
                ),
                "vod_play_from": "线路一",
            }],
        })

        results = telegram_bot.candidates("Example Series")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["kind"], "tv")
        self.assertEqual(len(results[0]["eps"]), 2)
        self.assertIn("2集", telegram_bot.label(results[0]))

    def test_single_episode_short_drama_is_recognized_as_tv(self):
        kind = telegram_bot.seasons.infer_item_kind(
            "movie",
            "Example Short Drama",
            {
                "type_name": "短剧",
                "vod_remarks": "更新至12集",
                "vod_play_url": "第1集$https://media.example/ep1.m3u8",
            },
        )

        self.assertEqual(kind, "tv")

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_movie_with_multiple_playback_urls_stays_a_movie(
        self, mock_http_json, _cookie
    ):
        self.set_search_responses(mock_http_json, {
            "list": [{
                "vod_name": "Example Movie",
                "vod_year": "2024",
                "type_name": "欧美",
                "vod_play_url": (
                    "播放1$https://media.example/720p.m3u8#"
                    "播放2$https://media.example/1080p.m3u8"
                ),
                "vod_play_from": "线路一",
            }],
        })

        results = telegram_bot.candidates("Example Movie")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["kind"], "movie")
        self.assertIn("电影", telegram_bot.label(results[0]))

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_same_series_with_separate_episode_records_is_merged(
        self, mock_http_json, _cookie
    ):
        self.set_search_responses(mock_http_json, {
            "list": [
                {
                    "vod_name": "Example Series 第1集",
                    "vod_year": "2024",
                    "type_name": "国产",
                    "vod_play_url": "第1集$https://media.example/ep1.m3u8",
                    "vod_play_from": "线路一",
                },
                {
                    "vod_name": "Example Series 第2集",
                    "vod_year": "2024",
                    "type_name": "国产",
                    "vod_play_url": "第2集$https://media.example/ep2.m3u8",
                    "vod_play_from": "线路一",
                },
            ],
        })

        results = telegram_bot.candidates("Example Series")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Example Series")
        self.assertEqual(results[0]["kind"], "tv")
        self.assertEqual(
            results[0]["eps"],
            [
                ("第1集", "https://media.example/ep1.m3u8"),
                ("第2集", "https://media.example/ep2.m3u8"),
            ],
        )
        self.assertIn("2集", telegram_bot.label(results[0]))

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_same_title_separate_episode_urls_are_marked_as_tv(
        self, mock_http_json, _cookie
    ):
        self.set_search_responses(mock_http_json, {
            "list": [
                {
                    "vod_name": "Example Series",
                    "vod_year": "2024",
                    "type_name": "国产",
                    "vod_play_url": "第1集$https://media.example/ep1.M3U8",
                    "vod_play_from": "线路一",
                },
                {
                    "vod_name": "Example Series",
                    "vod_year": "2024",
                    "type_name": "国产",
                    "vod_play_url": "第2集$https://media.example/ep2.M3U8",
                    "vod_play_from": "线路一",
                },
            ],
        })

        results = telegram_bot.candidates("Example Series")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["kind"], "tv")
        self.assertEqual(len(results[0]["eps"]), 2)

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_duplicate_urls_within_one_cms_result_are_removed(
        self, mock_http_json, _cookie
    ):
        self.set_search_responses(mock_http_json, {
            "list": [{
                "vod_name": "Example Movie",
                "vod_year": "2024",
                "vod_play_url": (
                    "播放$https://media.example/movie.m3u8#"
                    "重复播放$https://media.example/movie.m3u8"
                ),
                "vod_play_from": "线路一",
            }],
        })

        results = telegram_bot.candidates("Example Movie")

        self.assertEqual(
            results[0]["lines"][0]["eps"],
            [("播放", "https://media.example/movie.m3u8")],
        )

    @patch.object(telegram_bot, "get_decotv_cookie", return_value="auth=session-cookie")
    @patch.object(telegram_bot, "http_json")
    def test_same_series_with_multiple_seasons_is_marked_as_tv(
        self, mock_http_json, _cookie
    ):
        self.set_search_responses(mock_http_json, {
            "list": [
                {
                    "vod_name": "Example Series 第1季",
                    "vod_year": "2023",
                    "type_name": "国产",
                    "vod_play_url": "第1集$https://media.example/s1e1.m3u8",
                    "vod_play_from": "线路一",
                },
                {
                    "vod_name": "Example Series 第2季",
                    "vod_year": "2024",
                    "type_name": "国产",
                    "vod_play_url": "第1集$https://media.example/s2e1.m3u8",
                    "vod_play_from": "线路一",
                },
            ],
        })

        results = telegram_bot.candidates("Example Series")

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["kind"] == "tv" for result in results))
        self.assertEqual({result["season"] for result in results}, {1, 2})

    @patch.object(telegram_bot.urllib.request, "urlopen")
    def test_decotv_cookie_login_uses_admin_credentials(self, mock_urlopen):
        telegram_bot.cfg["decotv"].update({
            "admin_username": "admin",
            "admin_password": "admin-secret",
        })

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

    def test_find_library_items_matches_movie_and_season(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            movie_root = root / "movies"
            tv_root = root / "tv"
            movie_dir = movie_root / "Example Movie (2024)"
            movie_dir.mkdir(parents=True)
            (movie_dir / "Example Movie (2024).strm").write_text(
                "https://media.example/movie.m3u8\n", encoding="utf-8"
            )
            season_dir = tv_root / "Example Series (2023)" / "Season 02"
            season_dir.mkdir(parents=True)
            (season_dir / "Example Series S02E01.strm").write_text(
                "https://media.example/ep1.m3u8\n", encoding="utf-8"
            )

            with patch.object(telegram_bot.seasons, "MOVIE_ROOT", str(movie_root)), patch.object(
                telegram_bot.seasons, "TV_ROOT", str(tv_root)
            ):
                movie_matches = telegram_bot.find_library_items("Example Movie")
                tv_matches = telegram_bot.find_library_items("Example Series 第2季")

        self.assertEqual(len(movie_matches), 1)
        self.assertEqual(movie_matches[0]["kind"], "movie")
        self.assertEqual(len(tv_matches), 1)
        self.assertEqual(tv_matches[0]["season"], 2)
        self.assertEqual(len(tv_matches[0]["files"]), 1)

    def test_delete_item_only_removes_strm_files_and_keeps_library_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            movie_root = root / "movies"
            movie_dir = movie_root / "Example Movie (2024)"
            movie_dir.mkdir(parents=True)
            strm_file = movie_dir / "Example Movie (2024).strm"
            strm_file.write_text("https://media.example/movie.m3u8\n", encoding="utf-8")
            other_file = movie_dir / "poster.jpg"
            other_file.write_bytes(b"poster")
            item = {
                "kind": "movie",
                "path": str(movie_dir),
                "files": [str(strm_file)],
            }

            with patch.object(telegram_bot.seasons, "MOVIE_ROOT", str(movie_root)):
                deleted = telegram_bot.delete_item(item)

            self.assertEqual(deleted, 1)
            self.assertFalse(strm_file.exists())
            self.assertTrue(other_file.exists())
            self.assertTrue(movie_root.exists())
            self.assertTrue(movie_dir.exists())

    def test_update_item_source_replaces_each_strm_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tv_root = root / "tv"
            season_dir = tv_root / "Example Series" / "Season 01"
            season_dir.mkdir(parents=True)
            files = []
            for episode in (1, 2):
                path = season_dir / f"Example Series S01E{episode:02d}.strm"
                path.write_text(f"https://old.example/ep{episode}.m3u8\n", encoding="utf-8")
                files.append(str(path))
            item = {"kind": "tv", "files": files}
            line = {
                "eps": [("第1集", "https://new.example/ep1.m3u8")],
                "series_eps": [
                    ("第1集", "https://new.example/ep1.m3u8"),
                    ("第2集", "https://new.example/ep2.m3u8"),
                ]
            }

            with patch.object(telegram_bot.seasons, "TV_ROOT", str(tv_root)):
                count = telegram_bot.update_item_source(item, line)

            self.assertEqual(count, 2)
            self.assertEqual(
                [Path(path).read_text(encoding="utf-8").strip() for path in files],
                ["https://new.example/ep1.m3u8", "https://new.example/ep2.m3u8"],
            )

    def test_update_item_source_rejects_incomplete_tv_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tv_root = root / "tv"
            season_dir = tv_root / "Example Series" / "Season 01"
            season_dir.mkdir(parents=True)
            paths = []
            for episode in (1, 2):
                path = season_dir / f"Example Series S01E{episode:02d}.strm"
                path.write_text("https://old.example/ep.m3u8\n", encoding="utf-8")
                paths.append(str(path))

            with patch.object(telegram_bot.seasons, "TV_ROOT", str(tv_root)):
                with self.assertRaisesRegex(ValueError, "集数与本地 STRM 文件数不一致"):
                    telegram_bot.update_item_source(
                        {"kind": "tv", "files": paths},
                        {"eps": [("第1集", "https://new.example/ep1.m3u8")]},
                    )

            self.assertTrue(all("old.example" in Path(path).read_text(encoding="utf-8") for path in paths))

    def test_delete_command_requires_confirmation_before_unlinking(self):
        with tempfile.TemporaryDirectory() as temporary:
            movie_root = Path(temporary) / "movies"
            movie_dir = movie_root / "Example Movie (2024)"
            movie_dir.mkdir(parents=True)
            strm_file = movie_dir / "Example Movie (2024).strm"
            strm_file.write_text("https://media.example/movie.m3u8\n", encoding="utf-8")
            original_allowed = telegram_bot.ALLOWED
            telegram_bot.ALLOWED = {123}
            try:
                with patch.object(telegram_bot.seasons, "MOVIE_ROOT", str(movie_root)), patch.object(
                    telegram_bot, "tg"
                ) as mock_tg:
                    telegram_bot.handle({
                        "message": {"chat": {"id": 123}, "text": "/delete Example Movie"}
                    })
                    selection_markup = mock_tg.call_args.kwargs["reply_markup"]
                    selection_callback = selection_markup["inline_keyboard"][0][0]["callback_data"]
                    self.assertTrue(strm_file.exists())
                    telegram_bot.handle({
                        "callback_query": {
                            "id": "selection",
                            "message": {"chat": {"id": 123}},
                            "data": selection_callback,
                        }
                    })
                    confirmation_markup = mock_tg.call_args.kwargs["reply_markup"]
                    confirmation_callback = confirmation_markup["inline_keyboard"][0][0]["callback_data"]
                    self.assertTrue(strm_file.exists())
                    telegram_bot.handle({
                        "callback_query": {
                            "id": "confirmation",
                            "message": {"chat": {"id": 123}},
                            "data": confirmation_callback,
                        }
                    })
                    self.assertFalse(strm_file.exists())
            finally:
                telegram_bot.ALLOWED = original_allowed

    def test_edit_options_show_measured_line_quality(self):
        item = {
            "kind": "movie",
            "name": "Example Movie",
            "full": "Example Movie",
            "year": "2024",
            "season": 1,
            "files": ["C:\\media\\movies\\Example Movie.strm"],
        }
        result = {
            "name": "Example Movie",
            "full": "Example Movie",
            "season": 1,
            "explicit": False,
            "year": "2024",
            "kind": "movie",
            "lines": [{
                "label": "Source/1080p",
                "eps": [("播放", "https://media.example/1080p.m3u8")],
            }],
        }
        with patch.object(telegram_bot, "candidates", return_value=[result]), patch.object(
            telegram_bot.probe,
            "rank",
            side_effect=lambda lines, _log: [
                {**lines[0], "_info": "1920x1080 / 8.0Mbps"}
            ],
        ), patch.object(telegram_bot, "say") as mock_say:
            telegram_bot.show_edit_sources(123, item)

        markup = mock_say.call_args.args[2]
        self.assertIn("1920x1080 / 8.0Mbps", markup[0][0]["text"])
        self.assertTrue(markup[0][0]["callback_data"].startswith("editsrc:"))

    def test_add_flow_shows_measured_routes_and_adds_the_selected_route(self):
        candidate = {
            "name": "Example Movie",
            "full": "Example Movie",
            "season": 1,
            "explicit": False,
            "year": "2024",
            "kind": "movie",
            "eps": [("播放", "https://media.example/first.m3u8")],
            "lines": [
                {"label": "Source One/线路一", "eps": [("播放", "https://one.example/movie.m3u8")]},
                {"label": "Source Two/线路二", "eps": [("播放", "https://two.example/movie.m3u8")]},
            ],
        }
        ranked = [
            {
                **candidate["lines"][0],
                "_info": "1080p / 8.0Mbps, 首包0.42s",
            },
            {
                **candidate["lines"][1],
                "_info": "720p / 5.0Mbps, 首包0.81s",
            },
        ]
        previous_pending = telegram_bot.pending_actions.copy()
        original_allowed = telegram_bot.ALLOWED
        telegram_bot.pending_actions.clear()
        telegram_bot.ALLOWED = {123}
        try:
            with patch.object(
                telegram_bot.probe, "rank", return_value=ranked
            ), patch.object(telegram_bot, "say") as mock_say:
                telegram_bot.show_add_sources(123, candidate)

            prompt, buttons = mock_say.call_args.args[1:3]
            self.assertIn("清晰度", prompt)
            self.assertIn("首包响应时间", prompt)
            self.assertIn("Source One", buttons[0][0]["text"])
            self.assertIn("1080p", buttons[0][0]["text"])
            self.assertIn("0.42s", buttons[0][0]["text"])
            self.assertIn("Source Two", buttons[1][0]["text"])
            callback_data = buttons[1][0]["callback_data"]

            with patch.object(telegram_bot, "tg"), patch.object(
                telegram_bot, "do_add"
            ) as mock_do_add:
                telegram_bot.handle({
                    "callback_query": {
                        "id": "route-selection",
                        "message": {"chat": {"id": 123}},
                        "data": callback_data,
                    }
                })

            mock_do_add.assert_called_once_with(123, candidate, ranked[1])
            self.assertNotIn(123, telegram_bot.pending_actions)
        finally:
            telegram_bot.pending_actions.clear()
            telegram_bot.pending_actions.update(previous_pending)
            telegram_bot.ALLOWED = original_allowed

    def test_direct_add_search_still_requires_route_selection(self):
        candidate = {
            "name": "Example Movie",
            "season": 1,
        }
        with patch.object(telegram_bot, "candidates", return_value=[candidate]), patch.object(
            telegram_bot, "show_add_sources"
        ) as mock_show, patch.object(telegram_bot, "do_add") as mock_do_add, patch.object(
            telegram_bot, "say"
        ):
            telegram_bot.search_and_reply(123, "Example Movie", direct=True)

        mock_show.assert_called_once_with(123, candidate)
        mock_do_add.assert_not_called()

    def test_same_title_from_different_sources_has_distinct_search_buttons(self):
        candidates = [
            {
                "name": "Example Movie",
                "full": "Example Movie",
                "season": 1,
                "explicit": False,
                "year": "2024",
                "kind": "movie",
                "eps": [("播放", "https://one.example/movie.m3u8")],
                "lines": [{"label": "Source One", "eps": [("播放", "https://one.example/movie.m3u8")]}],
            },
            {
                "name": "Example Movie",
                "full": "Example Movie",
                "season": 1,
                "explicit": False,
                "year": "2024",
                "kind": "movie",
                "eps": [("播放", "https://two.example/movie.m3u8")],
                "lines": [{"label": "Source Two", "eps": [("播放", "https://two.example/movie.m3u8")]}],
            },
        ]

        with patch.object(telegram_bot, "candidates", return_value=candidates), patch.object(
            telegram_bot, "say"
        ) as mock_say:
            telegram_bot.search_and_reply(123, "Example Movie")

        buttons = mock_say.call_args.args[2]
        self.assertIn("Source One", buttons[0][0]["text"])
        self.assertIn("Source Two", buttons[1][0]["text"])
        self.assertNotEqual(buttons[0][0]["text"], buttons[1][0]["text"])

    def test_edit_series_probes_first_episode_and_retains_full_source(self):
        item = {
            "kind": "tv",
            "name": "Example Series",
            "full": "Example Series",
            "year": "2024",
            "season": 1,
            "files": [
                "C:\\media\\tv\\Example Series\\Season 01\\S01E01.strm",
                "C:\\media\\tv\\Example Series\\Season 01\\S01E02.strm",
            ],
        }
        result = {
            "name": "Example Series",
            "full": "Example Series",
            "season": 1,
            "explicit": True,
            "year": "2024",
            "kind": "tv",
            "lines": [{
                "label": "Source/线路一",
                "eps": [
                    ("第1集", "https://media.example/ep1.m3u8"),
                    ("第2集", "https://media.example/ep2.m3u8"),
                ],
            }],
        }
        previous_pending = telegram_bot.pending_actions.copy()
        telegram_bot.pending_actions.clear()
        try:
            with patch.object(telegram_bot, "candidates", return_value=[result]), patch.object(
                telegram_bot.probe, "rank", side_effect=lambda lines, _log: lines
            ) as mock_rank, patch.object(telegram_bot, "say"):
                telegram_bot.show_edit_sources(123, item)

            ranked_candidates = mock_rank.call_args.args[0]
            self.assertEqual(
                ranked_candidates[0]["eps"],
                [("第1集", "https://media.example/ep1.m3u8")],
            )
            self.assertEqual(
                ranked_candidates[0]["series_eps"],
                [
                    ("第1集", "https://media.example/ep1.m3u8"),
                    ("第2集", "https://media.example/ep2.m3u8"),
                ],
            )
            self.assertEqual(
                telegram_bot.pending_actions[123]["lines"][0]["series_eps"],
                ranked_candidates[0]["series_eps"],
            )
        finally:
            telegram_bot.pending_actions.clear()
            telegram_bot.pending_actions.update(previous_pending)

    def test_help_lists_delete_and_edit_commands(self):
        original_allowed = telegram_bot.ALLOWED
        telegram_bot.ALLOWED = {123}
        try:
            with patch.object(telegram_bot, "tg") as mock_tg:
                telegram_bot.handle({
                    "message": {"chat": {"id": 123}, "text": "/help"}
                })
        finally:
            telegram_bot.ALLOWED = original_allowed

        help_text = mock_tg.call_args.kwargs["text"]
        self.assertIn("/delete 片名", help_text)
        self.assertIn("/edit 片名", help_text)
        self.assertIn("分辨率", help_text)

    def test_season_migration_previews_before_confirmation_and_applies_on_click(self):
        preview = subprocess.CompletedProcess(
            ["migrate_seasons.py"], 0,
            "[预览]: Example S02 (2集) -> Example/Season 02\n共 1 个季目录待迁移(加 --apply 执行)\n",
            "",
        )
        applied = subprocess.CompletedProcess(
            ["migrate_seasons.py", "--apply"], 0,
            "移动: Example S02 (2集) -> Example/Season 02\n共 1 个季目录已迁移\n",
            "",
        )
        original_allowed = telegram_bot.ALLOWED
        telegram_bot.ALLOWED = {123}
        try:
            with patch.object(telegram_bot.subprocess, "run", side_effect=[preview, applied]) as mock_run, patch.object(
                telegram_bot, "tg"
            ) as mock_tg, patch.object(telegram_bot, "refresh_jellyfin") as mock_refresh:
                telegram_bot.handle({
                    "message": {"chat": {"id": 123}, "text": "/migrate"}
                })

                self.assertEqual(mock_run.call_count, 1)
                self.assertEqual(mock_run.call_args.args[0][-1], "/opt/m3u/migrate_seasons.py")
                markup = mock_tg.call_args.kwargs["reply_markup"]
                callback_data = markup["inline_keyboard"][0][0]["callback_data"]
                self.assertTrue(callback_data.startswith("migrateapply:"))

                telegram_bot.handle({
                    "callback_query": {
                        "id": "migration",
                        "message": {"chat": {"id": 123}},
                        "data": callback_data,
                    }
                })

                self.assertEqual(mock_run.call_count, 2)
                self.assertEqual(mock_run.call_args.args[0][-1], "--apply")
                mock_refresh.assert_called_once()
        finally:
            telegram_bot.ALLOWED = original_allowed

    def test_season_migration_does_not_offer_confirmation_when_nothing_is_pending(self):
        result = subprocess.CompletedProcess(
            ["migrate_seasons.py"], 0,
            "共 0 个季目录待迁移(加 --apply 执行)\n",
            "",
        )
        with patch.object(telegram_bot.subprocess, "run", return_value=result), patch.object(
            telegram_bot, "say"
        ) as mock_say:
            telegram_bot.run_season_migration(123)

        self.assertEqual(mock_say.call_count, 1)
        self.assertEqual(len(mock_say.call_args.args), 2)
        self.assertIn("共 0 个季目录待迁移", mock_say.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
