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
    import auto
    import crawl


def record(name="示例剧", urls=("https://a.example/1.m3u8", "https://a.example/2.m3u8")):
    play = "#".join(f"第{i}集${url}" for i, url in enumerate(urls, 1))
    return {
        "vod_name": name,
        "vod_year": "2024",
        "type_name": "国产剧",
        "vod_play_from": "m3u8",
        "vod_play_url": play,
    }


def first_choice(choices, log=print):
    return choices[0] if choices else None


class CrawlOwnRecordTests(unittest.TestCase):
    def setUp(self):
        crawl._PLAYBACK_CACHE.clear()

    def test_uses_source_record_without_searching(self):
        with patch.object(crawl.probe, "pick", side_effect=first_choice), \
                patch.object(crawl.decotv_search, "search_all") as search_all:
            selected = crawl.find_playback(
                "示例剧", "2024", "tv", own=({"name": "源A"}, record())
            )
        self.assertFalse(search_all.called)
        self.assertEqual(len(selected["eps"]), 2)
        self.assertTrue(selected["label"].startswith("源A/"))

    def test_falls_back_to_aggregate_search_when_record_has_no_m3u8(self):
        broken = record(urls=("https://a.example/1.mp4",))
        found = record(urls=("https://b.example/1.m3u8",))
        with patch.object(crawl.probe, "pick", side_effect=first_choice), \
                patch.object(crawl, "get_cookie", return_value=""), \
                patch.object(crawl.decotv_search, "get_sources", return_value=[]), \
                patch.object(
                    crawl.decotv_search, "search_all", return_value=[({"name": "源B"}, found)]
                ) as search_all:
            selected = crawl.find_playback(
                "示例剧", "2024", "tv", own=({"name": "源A"}, broken)
            )
        self.assertTrue(search_all.called)
        self.assertTrue(selected["label"].startswith("源B/"))

    def test_falls_back_when_every_own_line_fails_probe(self):
        found = record(urls=("https://b.example/1.m3u8",))
        picks = iter([None, "ok"])

        def pick(choices, log=print):
            result = next(picks)
            return choices[0] if result == "ok" else None

        with patch.object(crawl.probe, "pick", side_effect=pick), \
                patch.object(crawl, "get_cookie", return_value=""), \
                patch.object(crawl.decotv_search, "get_sources", return_value=[]), \
                patch.object(
                    crawl.decotv_search, "search_all", return_value=[({"name": "源B"}, found)]
                ):
            selected = crawl.find_playback(
                "示例剧", "2024", "tv", own=({"name": "源A"}, record())
            )
        self.assertTrue(selected["label"].startswith("源B/"))


class AutoLookupTests(unittest.TestCase):
    def test_lookup_buffers_logs_and_returns_hit(self):
        def fake_search(title, year, sources, cookie, log):
            log("搜索中", title)
            return [{"label": "x"}]

        with patch.object(auto, "search", side_effect=fake_search), \
                patch.object(auto.probe, "pick", side_effect=first_choice):
            hit, lines, ok = auto.lookup("示例", "2024", [], "")
        self.assertTrue(ok)
        self.assertEqual(hit, {"label": "x"})
        self.assertEqual(lines, ["搜索中 示例"])

    def test_lookup_error_is_not_reported_as_a_miss(self):
        with patch.object(auto, "search", side_effect=RuntimeError("boom")):
            hit, lines, ok = auto.lookup("示例", "2024", [], "")
        self.assertFalse(ok)
        self.assertIsNone(hit)
        self.assertTrue(lines)


if __name__ == "__main__":
    unittest.main()
