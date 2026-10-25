import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import probe


class ProbeTests(unittest.TestCase):
    def test_master_playlist_returns_resolution_for_selected_variant(self):
        playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080
high/index.m3u8
"""

        url, resolution = probe._parse_master(
            playlist, "https://media.example/master.m3u8"
        )

        self.assertEqual(url, "https://media.example/high/index.m3u8")
        self.assertEqual(resolution, "1920x1080")

    def test_non_http_urls_are_rejected_without_opening_them(self):
        for url in ("file:///etc/passwd#.m3u8", "ftp://host/a.m3u8"):
            self.assertIsNone(probe.score(url))

    def test_rank_probes_routes_concurrently_and_orders_by_headroom(self):
        def fake_score(url):
            return {
                "https://a/slow.m3u8": {"ttfb": 0.5, "mbps": 3.0, "need": 2.0, "headroom": 1.5, "resolution": ""},
                "https://b/fast.m3u8": {"ttfb": 0.2, "mbps": 10.0, "need": 2.0, "headroom": 5.0, "resolution": "1920x1080"},
            }.get(url)

        candidates = [
            {"label": "slow", "eps": [("1", "https://a/slow.m3u8")]},
            {"label": "dead", "eps": [("1", "https://c/dead.m3u8")]},
            {"label": "fast", "eps": [("1", "https://b/fast.m3u8")]},
        ]
        with unittest.mock.patch.object(probe, "score", side_effect=fake_score):
            ranked = probe.rank(candidates, lambda *_: None)

        self.assertEqual([c["label"] for c in ranked], ["fast", "slow"])
        self.assertIn("1920x1080", ranked[0]["_info"])


if __name__ == "__main__":
    unittest.main()
