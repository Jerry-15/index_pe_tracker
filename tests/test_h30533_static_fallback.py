import json
import unittest

from etl.pipeline_us import USETL


class H30533StaticFallbackTests(unittest.TestCase):
    def test_empty_live_snapshot_loads_published_static_series(self):
        frame = USETL._load_static_fallback()

        self.assertFalse(frame.empty)
        self.assertGreater(frame.iloc[0]["pe_ratio"], 0)
        self.assertEqual(frame.iloc[0]["source_type"], "published_static_snapshot")
        self.assertIn("live H30533 source unavailable", frame.iloc[0]["validity_reason"])
