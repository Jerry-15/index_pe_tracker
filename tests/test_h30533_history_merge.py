import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cache.db import CacheDB


class H30533HistoryMergeTests(unittest.TestCase):
    def test_live_snapshot_is_merged_with_legacy_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "h30533.db")
            historical = pd.DataFrame([
                {"trade_date": "2026-10-13", "pe_ratio": 20.0},
                {"trade_date": "2026-10-14", "pe_ratio": 21.0},
            ])
            live = pd.DataFrame([{
                "trade_date": "2026-10-15", "pe_ratio": 22.0,
                "index_code": "H30533", "source_type": "live",
                "source_date": "2026-10-15", "constituent_as_of": "2026-10-15",
            }])
            cache.save_us_pe_daily(historical)
            cache.save_h30533_snapshot(
                "H30533", pd.DataFrame([{"security_code": "A", "weight": 1.0,
                                          "index_code": "H30533", "source_type": "fixture", "as_of": "2026-10-15"}]),
                live, pd.DataFrame([{"security_code": "A", "weight": 1.0,
                                     "index_code": "H30533", "source_type": "fixture", "source_date": "2026-10-15"}]),
            )

            result = cache.load_index_series("H30533")

        self.assertEqual(result["trade_date"].tolist(), ["2026-10-13", "2026-10-14", "2026-10-15"])
