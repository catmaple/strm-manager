import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import seasons


class PlayLineParsingTests(unittest.TestCase):
    def test_parse_play_lines_keeps_only_http_m3u8_and_drops_extras(self):
        record = {
            "vod_play_from": "线路一$$$线路二$$$",
            "vod_play_url": (
                "第1集$https://a.example/1.m3u8#预告$https://a.example/t.m3u8#"
                "第2集$https://a.example/2.m3u8$$$"
                "第1集$https://b.example/1.mp4$$$"
                "第1集$file:///etc/passwd.m3u8#第2集$HTTP://c.example/2.M3U8"
            ),
        }

        lines = seasons.parse_play_lines(record)

        self.assertEqual(
            lines,
            [
                ("线路一", [("第1集", "https://a.example/1.m3u8"), ("第2集", "https://a.example/2.m3u8")]),
                ("线路2", [("第2集", "HTTP://c.example/2.M3U8")]),
            ],
        )

    def test_parse_play_lines_handles_missing_fields(self):
        self.assertEqual(seasons.parse_play_lines({}), [])
        self.assertEqual(seasons.parse_play_lines({"vod_play_url": None}), [])


class EpisodeNumberingTests(unittest.TestCase):
    def numbers(self, names):
        return [n for n, _ in seasons.number_episodes([(name, f"u{i}") for i, name in enumerate(names)])]

    def test_numbers_follow_episode_names_so_gaps_do_not_shift_later_episodes(self):
        self.assertEqual(self.numbers(["第1集", "第2集", "第4集"]), [1, 2, 4])
        self.assertEqual(self.numbers(["EP01", "EP02"]), [1, 2])
        self.assertEqual(self.numbers(["01", "02", "03"]), [1, 2, 3])
        self.assertEqual(self.numbers(["第一集", "第二集", "第十集"]), [1, 2, 10])

    def test_unparseable_or_duplicate_names_fall_back_to_position(self):
        self.assertEqual(self.numbers(["正片", "番外"]), [1, 2])
        self.assertEqual(self.numbers(["第1集", "第1集"]), [1, 2])
        self.assertEqual(self.numbers(["第1集", "大结局"]), [1, 2])

    def test_continuous_cumulative_numbers_restart_at_one(self):
        self.assertEqual(self.numbers(["第13集", "第14集", "第15集"]), [1, 2, 3])
        # 有缺集时保留真实集号
        self.assertEqual(self.numbers(["第13集", "第15集"]), [13, 15])

    def test_write_strm_uses_episode_numbers_and_skips_trailers(self):
        with tempfile.TemporaryDirectory() as temporary:
            tv_root = Path(temporary) / "tv"
            record = {
                "vod_play_url": (
                    "预告$https://a.example/trailer.m3u8#"
                    "第1集$https://a.example/1.m3u8#"
                    "第2集$https://a.example/2.m3u8#"
                    "第4集$https://a.example/4.m3u8"
                ),
            }
            (_, eps), = seasons.parse_play_lines(record)
            with patch.object(seasons, "TV_ROOT", str(tv_root)):
                folder, count = seasons.write_strm({
                    "name": "示例剧", "full": "示例剧", "season": 1, "explicit": True,
                    "year": "2024", "kind": "tv", "eps": eps,
                })

            season_dir = tv_root / "示例剧 (2024)" / "Season 01"
            self.assertEqual(count, 3)
            self.assertEqual(
                sorted(path.name for path in season_dir.iterdir()),
                ["示例剧 S01E01.strm", "示例剧 S01E02.strm", "示例剧 S01E04.strm"],
            )
            self.assertEqual(
                (season_dir / "示例剧 S01E04.strm").read_text(encoding="utf-8"),
                "https://a.example/4.m3u8\n",
            )


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_replaces_content_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "movie.strm"
            path.write_text("old\n", encoding="utf-8")

            seasons.atomic_write(str(path), "new\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual([item.name for item in Path(temporary).iterdir()], ["movie.strm"])

    @unittest.skipIf(os.name == "nt", "POSIX 权限位")
    def test_atomic_write_is_readable_by_other_users(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "movie.strm"
            seasons.atomic_write(str(path), "x\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_failed_replace_cleans_up_temp_file_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "movie.strm"
            path.write_text("old\n", encoding="utf-8")

            with patch.object(seasons.os, "replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    seasons.atomic_write(str(path), "new\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual([item.name for item in Path(temporary).iterdir()], ["movie.strm"])


if __name__ == "__main__":
    unittest.main()
