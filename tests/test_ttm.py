import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cache.db import CacheDB
from etl.pipeline import PEETL
from etl.quality import quality_level


class RegularValuationMetadataTests(unittest.TestCase):
    def test_regular_quality_levels_are_a_or_b_only_when_complete(self):
        for source_type in ("sina", "sw_official"):
            with self.subTest(source_type=source_type):
                self.assertEqual(quality_level(
                    source_type=source_type, weight_coverage=1.0,
                    financial_stale_count=0, derived_market_cap_count=0,
                    quote_disagreement_count=0,
                ), "A")
                self.assertEqual(quality_level(
                    source_type=source_type, weight_coverage=1.0,
                    financial_stale_count=0, derived_market_cap_count=1,
                    quote_disagreement_count=0,
                ), "B")

        self.assertEqual(quality_level(
            source_type="csi_official", weight_coverage=1.0,
            financial_stale_count=1, derived_market_cap_count=0,
            quote_disagreement_count=0,
        ), "C")

    def test_vectorized_pe_numeric_result_is_unchanged(self):
        class QuarterlyCache:
            def load_quarterly_profits(self, code):
                return pd.DataFrame([
                    {"report_date": pd.Timestamp("2025-12-31"), "quarter_profit": 100.0,
                     "publish_date": pd.Timestamp("2026-01-01")},
                    {"report_date": pd.Timestamp("2026-03-31"), "quarter_profit": 100.0,
                     "publish_date": pd.Timestamp("2026-04-01")},
                ])

        etl = PEETL.__new__(PEETL)
        etl.cache = QuarterlyCache()
        stocks = [{"code": f"60000{i}", "name": "Fixture"} for i in range(10)]
        quotes = {
            stock["code"]: pd.DataFrame([{
                "date": "2026-04-02", "close": 10.0, "outstanding_share": 100.0,
            }]) for stock in stocks
        }
        result = etl._vectorized_compute_pe(
            stocks,
            quotes,
        )

        self.assertEqual(result.iloc[0]["pe_ratio"], 5.0)

    def test_pe_values_and_metadata_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "test.db")
            daily = pd.DataFrame([{
                "trade_date": "2026-10-14",
                "total_market_cap": 123.0,
                "total_profit_ttm": 10.0,
                "pe_ratio": 12.3,
                "stock_count": 20,
                "quality_level": "B",
                "data_source": "fixture",
                "generated_at": "2026-10-15T00:00:00+00:00",
                "coverage_ratio": 0.8,
                "financial_stale_count": 1,
                "validity_reason": "partial coverage",
            }])
            cache.save_pe_daily("000300", daily)
            cache.save_pe_stats("000300", pd.DataFrame([{
                "trade_date": "2026-10-14",
                "pe_mean": 12.3,
                "quality_level": "B",
                "data_source": "fixture",
                "generated_at": "2026-10-15T00:00:00+00:00",
                "coverage_ratio": 0.8,
                "financial_stale_count": 1,
                "validity_reason": "partial coverage",
            }]))

            loaded = cache.load_pe_daily("000300")
            loaded_stats = cache.load_pe_stats("000300")

            self.assertEqual(loaded.iloc[0]["pe_ratio"], 12.3)
            self.assertEqual(loaded.iloc[0]["total_market_cap"], 123.0)
            self.assertEqual(loaded.iloc[0]["quality_level"], "B")
            self.assertEqual(loaded.iloc[0]["data_source"], "fixture")
            self.assertEqual(loaded.iloc[0]["generated_at"], "2026-10-15T00:00:00+00:00")
            self.assertEqual(loaded.iloc[0]["coverage_ratio"], 0.8)
            self.assertEqual(loaded.iloc[0]["financial_stale_count"], 1)
            self.assertEqual(loaded.iloc[0]["validity_reason"], "partial coverage")
            self.assertEqual(loaded_stats.iloc[0]["pe_mean"], 12.3)
            self.assertEqual(loaded_stats.iloc[0]["quality_level"], "B")
            self.assertEqual(loaded_stats.iloc[0]["validity_reason"], "partial coverage")

    def test_legacy_regular_table_and_extra_column_remain_loadable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE index_pe_daily (
                        trade_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        total_market_cap REAL, total_profit_ttm REAL,
                        pe_ratio REAL, stock_count INTEGER, updated_at REAL,
                        extra_legacy TEXT,
                        PRIMARY KEY (trade_date, index_code)
                    )
                """)
                conn.execute(
                    "INSERT INTO index_pe_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("2026-10-14", "000300", 123.0, 10.0, 12.3, 20, 1.0, "kept"),
                )

            cache = CacheDB(db_path)
            loaded = cache.load_pe_daily("000300")

            self.assertEqual(loaded.iloc[0]["pe_ratio"], 12.3)
            self.assertEqual(loaded.iloc[0]["extra_legacy"], "kept")
            self.assertIn("quality_level", loaded.columns)

    def test_repeated_daily_save_preserves_legacy_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE index_pe_daily (
                        trade_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        total_market_cap REAL, total_profit_ttm REAL,
                        pe_ratio REAL, stock_count INTEGER, updated_at REAL,
                        extra_legacy TEXT,
                        PRIMARY KEY (trade_date, index_code)
                    )
                """)
                conn.execute(
                    "INSERT INTO index_pe_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("2026-10-14", "000300", 123.0, 10.0, 12.3, 20, 1.0, "kept"),
                )

            cache = CacheDB(db_path)
            cache.save_pe_daily("000300", pd.DataFrame([{
                "trade_date": "2026-10-14", "pe_ratio": 13.0,
            }]))

            self.assertEqual(cache.load_pe_daily("000300").iloc[0]["pe_ratio"], 13.0)
            self.assertEqual(cache.load_pe_daily("000300").iloc[0]["extra_legacy"], "kept")

    def test_repeated_stats_save_preserves_legacy_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE index_pe_stats (
                        trade_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        pe_mean REAL, pe_std REAL, pe_high_line REAL,
                        pe_low_line REAL, pe_percentile REAL, updated_at REAL,
                        extra_legacy TEXT,
                        PRIMARY KEY (trade_date, index_code)
                    )
                """)
                conn.execute(
                    "INSERT INTO index_pe_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("2026-10-14", "000300", 12.0, 1.0, 13.0, 11.0, 50.0, 1.0, "kept"),
                )

            cache = CacheDB(db_path)
            cache.save_pe_stats("000300", pd.DataFrame([{
                "trade_date": "2026-10-14", "pe_mean": 13.0,
            }]))

            self.assertEqual(cache.load_pe_stats("000300").iloc[0]["pe_mean"], 13.0)
            self.assertEqual(cache.load_pe_stats("000300").iloc[0]["extra_legacy"], "kept")


if __name__ == "__main__":
    unittest.main()
