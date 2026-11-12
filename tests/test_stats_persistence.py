import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cache.db import CacheDB


class StatsPersistenceTests(unittest.TestCase):
    def test_stats_save_works_after_schema_migration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "stats.db")
            frame = pd.DataFrame([{
                "trade_date": "2026-10-15",
                "pe_mean": 12.0,
                "pe_std": 1.0,
                "pe_high_line": 13.0,
                "pe_low_line": 11.0,
                "pe_percentile": 40.0,
                "quality_level": "A",
                "data_source": "sina",
                "coverage_ratio": 0.99,
                "validity_reason": None,
                "constituent_count": 300,
                "valid_stock_count": 297,
            }])

            cache.save_pe_stats("801120", frame)

            loaded = cache.load_pe_stats("801120")
            self.assertEqual(loaded.iloc[0]["pe_mean"], 12.0)
            self.assertEqual(loaded.iloc[0]["valid_stock_count"], 297)

    def test_stats_save_is_idempotent_for_same_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "stats.db")
            frame = pd.DataFrame([{
                "trade_date": "2026-10-15", "pe_mean": 12.0, "pe_percentile": 40.0,
            }])

            cache.save_pe_stats("801120", frame)
            cache.save_pe_stats("801120", frame)

            self.assertEqual(len(cache.load_pe_stats("801120")), 1)


if __name__ == "__main__":
    unittest.main()
