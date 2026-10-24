import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
