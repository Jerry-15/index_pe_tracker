import unittest
from unittest.mock import Mock

import pandas as pd

from cache.db import CacheDB


class IndexSeriesLoaderTests(unittest.TestCase):
    def test_h30533_loader_prefers_legacy_history_when_snapshot_is_empty(self):
        cache = Mock(spec=CacheDB)
        cache.load_h30533_snapshot.return_value = {
            "daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame(),
        }
        cache.load_us_pe_daily.return_value = pd.DataFrame([{
            "trade_date": "2026-10-14", "pe_ratio": 13.0,
        }])
        cache.load_us_pe_stats.return_value = pd.DataFrame()

        result = CacheDB.load_index_series(cache, "H30533")

        self.assertEqual(result.iloc[0]["pe_ratio"], 13.0)

    def test_regular_loader_merges_statistics(self):
        cache = Mock(spec=CacheDB)
        cache.load_pe_daily.return_value = pd.DataFrame([{
            "trade_date": "2026-10-14", "pe_ratio": 13.0,
        }])
        cache.load_pe_stats.return_value = pd.DataFrame([{
            "trade_date": "2026-10-14", "pe_percentile": 40.0,
        }])

        result = CacheDB.load_index_series(cache, "000300")

        self.assertEqual(result.iloc[0]["pe_percentile"], 40.0)
