import sqlite3
import tempfile
import unittest
from pathlib import Path

from cache.db import CacheDB


class DatabaseSchemaTests(unittest.TestCase):
    def test_schema_migration_is_idempotent_and_keeps_legacy_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "legacy.db"
            with sqlite3.connect(path) as conn:
                conn.execute("""
                    CREATE TABLE index_pe_daily (
                        trade_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        pe_ratio REAL, updated_at REAL, extra_legacy TEXT,
                        PRIMARY KEY (trade_date, index_code)
                    )
                """)
                conn.execute(
                    "INSERT INTO index_pe_daily VALUES (?, ?, ?, ?, ?)",
                    ("2026-10-02", "000300", 12.3, 1.0, "kept"),
                )

            CacheDB(path)
            cache = CacheDB(path)
            with cache._conn() as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(index_pe_daily)")}
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}

            self.assertTrue({
                "constituent_count", "valid_stock_count", "market_cap_method",
                "constituent_basis", "financial_as_of",
            }.issubset(columns))
            self.assertIn("trading_calendar", tables)
            self.assertIn("update_checkpoint", tables)
            self.assertEqual(cache.load_pe_daily("000300").iloc[0]["extra_legacy"], "kept")
