import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cache.db import CacheDB


class TradingCalendarTests(unittest.TestCase):
    def test_calendar_roundtrip_preserves_open_flag_and_date_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "calendar.db")
            cache.save_trading_calendar(pd.DataFrame([
                {"trade_date": "2026-10-01", "is_open": 0, "source": "fixture"},
                {"trade_date": "2026-10-02", "is_open": 1, "source": "fixture"},
            ]))

            loaded = cache.load_trading_calendar("2026-10-01", "2026-10-02")
            self.assertEqual(loaded["trade_date"].tolist(), ["2026-10-01", "2026-10-02"])
            self.assertEqual(loaded["is_open"].tolist(), [0, 1])
            self.assertTrue(cache.has_trading_calendar())

    def test_empty_calendar_is_not_treated_as_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "calendar.db")
            self.assertFalse(cache.has_trading_calendar())
