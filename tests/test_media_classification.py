import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reclassify_media
import seasons


class MediaClassificationTests(unittest.TestCase):
    def test_explicit_kind_overrides_multiple_playback_urls(self):
        self.assertFalse(seasons.is_tv({
            "kind": "movie",
            "eps": [("Line 1", "url-1"), ("Line 2", "url-2")],
        }))
        self.assertTrue(seasons.is_tv({
            "kind": "tv",
            "eps": [("Episode 1", "url-1")],
        }))

    def test_catalog_movie_category_overrides_wrong_tv_mapping(self):
        self.assertEqual(
            seasons.infer_item_kind("tv", "示例影片", {"type_name": "动作电影"}),
            "movie",
        )

    def test_explicit_season_and_tv_category_remain_tv(self):
        self.assertEqual(
            seasons.infer_item_kind("movie", "示例影片 第2季", {"type_name": "电影"}),
            "tv",
        )
        self.assertEqual(
            seasons.infer_item_kind("movie", "示例剧集", {"type_name": "连续剧"}),
            "tv",
        )

    def test_unrecognized_catalog_category_keeps_configured_kind(self):
        self.assertEqual(
            seasons.infer_item_kind("tv", "示例节目", {"type_name": "华语"}),
            "tv",
        )

    def test_multiple_playback_urls_without_episode_labels_are_not_tv(self):
        item = {
            "type_name": "欧美",
            "vod_play_url": (
                "播放1$https://media.example/720p.m3u8#"
                "播放2$https://media.example/1080p.m3u8"
            ),
        }
        self.assertEqual(
            seasons.infer_item_kind("movie", "速度与激情 (2001)", item),
            "movie",
        )

    def test_episode_labeled_playback_urls_are_tv(self):
        item = {
            "type_name": "华语",
            "vod_play_url": (
                "第1集$https://media.example/ep1.m3u8#"
                "第2集$https://media.example/ep2.m3u8"
            ),
        }
        self.assertEqual(
            seasons.infer_item_kind("movie", "示例剧集", item),
            "tv",
        )

    def test_movie_sequel_part_number_is_not_treated_as_tv(self):
        for title in ("功夫熊猫 第2部", "疯狂动物城 第二部"):
            self.assertEqual(
                seasons.infer_item_kind("movie", title, {"type_name": "动画片"}),
                "movie",
            )
        self.assertEqual(
            seasons.infer_item_kind("movie", "示例剧集 第2部", {"type_name": "国产剧"}),
            "tv",
        )

    def test_animated_and_tv_movie_categories_are_movies(self):
        for category in ("动漫电影", "动画电影", "电视电影", "剧场版", "喜剧片", "剧情片"):
            self.assertEqual(
                seasons.infer_item_kind("tv", "示例", {"type_name": category}),
                "movie",
                category,
            )

    def test_regional_drama_categories_are_tv_even_with_one_episode(self):
        for category in ("韩剧", "国产剧", "欧美剧", "港台剧", "日剧", "泰剧", "海外剧"):
            self.assertEqual(
                seasons.infer_item_kind(
                    "movie", "示例", {
                        "type_name": category,
                        "vod_play_url": "HD$https://media.example/1.m3u8",
                    },
                ),
                "tv",
                category,
            )

    def test_bare_genre_and_documentary_categories_keep_configured_kind(self):
        for category in ("喜剧", "纪录片"):
            self.assertEqual(
                seasons.infer_item_kind("movie", "示例", {"type_name": category}),
                "movie",
                category,
            )

    def test_theatrical_title_under_anime_category_is_movie(self):
        self.assertEqual(
            seasons.infer_item_kind("tv", "名侦探柯南 剧场版", {"type_name": "动漫"}),
            "movie",
        )
        self.assertEqual(
            seasons.infer_item_kind("tv", "示例番剧", {"type_name": "动漫"}),
            "tv",
        )

    def test_numbered_episode_names_are_tv_but_version_names_are_not(self):
        numbered = {"vod_play_url": "01$https://a/1.m3u8#02$https://a/2.m3u8"}
        versions = {"vod_play_url": "国语$https://a/1.m3u8#粤语$https://a/2.m3u8"}
        self.assertEqual(seasons.infer_item_kind("movie", "示例", numbered), "tv")
        self.assertEqual(seasons.infer_item_kind("movie", "示例", versions), "movie")
        self.assertEqual(
            seasons.infer_item_kind(
                "movie", "示例", {"vod_play_url": "1$https://a/1.m3u8"}
            ),
            "movie",
        )

    @patch.object(reclassify_media, "category_kinds", return_value={"1": "movie"})
    @patch.object(
        reclassify_media,
        "get_json",
        return_value={
            "list": [{
                "vod_name": "示例电影",
                "vod_year": "2024",
                "type_id": "1",
                "vod_play_url": "播放$https://media.example/movie.m3u8",
            }]
        },
    )
    def test_movie_requires_exact_cms_playback_url_match(self, _get_json, _category_kinds):
        match = reclassify_media.find_movie_match(
            "示例电影", "2024", "https://media.example/movie.m3u8",
            [{"api": "https://cms.example/api.php/provide/vod"}],
        )
        self.assertEqual(match, {"title": "示例电影", "year": "2024"})

    def test_auto_reclassifies_single_verified_movie_strm(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tv_root = root / "tv"
            movie_root = root / "movies"
            season_dir = tv_root / "示例电影 (2024)" / "Season 01"
            season_dir.mkdir(parents=True)
            source_file = season_dir / "示例电影 S01E01.strm"
            source_file.write_text("https://media.example/movie.m3u8\n", encoding="utf-8")
            candidate = next(reclassify_media.movie_candidate_directories(str(tv_root)))
            match = {"title": "示例电影", "year": "2024"}

            moved, message = reclassify_media.move_candidate(
                candidate, match, str(movie_root)
            )

            target_file = movie_root / "示例电影 (2024)" / "示例电影 (2024).strm"
            self.assertTrue(moved)
            self.assertIn("移动到", message)
            self.assertTrue(target_file.is_file())
            self.assertEqual(
                target_file.read_text(encoding="utf-8"),
                "https://media.example/movie.m3u8\n",
            )
            self.assertFalse((tv_root / "示例电影 (2024)").exists())

    def test_auto_cleanup_skips_existing_different_movie_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tv_root = root / "tv"
            movie_root = root / "movies"
            season_dir = tv_root / "示例电影 (2024)" / "Season 01"
            season_dir.mkdir(parents=True)
            source_file = season_dir / "示例电影 S01E01.strm"
            source_file.write_text("https://media.example/new.m3u8\n", encoding="utf-8")
            destination_dir = movie_root / "示例电影 (2024)"
            destination_dir.mkdir(parents=True)
            (destination_dir / "示例电影 (2024).strm").write_text(
                "https://media.example/other.m3u8\n", encoding="utf-8"
            )
            candidate = next(reclassify_media.movie_candidate_directories(str(tv_root)))

            moved, message = reclassify_media.move_candidate(
                candidate, {"title": "示例电影", "year": "2024"}, str(movie_root)
            )

            self.assertFalse(moved)
            self.assertIn("已存在不同", message)
            self.assertTrue(source_file.is_file())


if __name__ == "__main__":
    unittest.main()
