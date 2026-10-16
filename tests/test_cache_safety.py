import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cache.db import CacheDB


class CacheSafetyTests(unittest.TestCase):
    def test_quote_revision_replaces_existing_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "quotes.db")
            first = pd.DataFrame([{"date": "2026-10-14", "close": 10.0, "outstanding_share": 100.0}])
            revised = first.copy()
            revised.loc[0, "close"] = 12.0
            cache.upsert_quotes("600000", first)
            cache.upsert_quotes("600000", revised)

            self.assertEqual(cache.load_quotes("600000").iloc[0]["close"], 12.0)

    def test_clear_index_keeps_shared_quotes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "clear.db")
            quotes = pd.DataFrame([{"date": "2026-10-14", "close": 10.0, "outstanding_share": 100.0}])
            cache.upsert_quotes("600000", quotes)
            cache.clear_index("000300")

            self.assertFalse(cache.load_quotes("600000").empty)
