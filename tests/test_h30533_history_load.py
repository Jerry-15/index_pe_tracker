import unittest
from unittest.mock import patch
from unittest.mock import Mock

import pandas as pd

from etl.pipeline_us import USETL


class H30533HistoryLoadTests(unittest.TestCase):
    def test_local_historical_series_precedes_static_fallback(self):
        etl = USETL.__new__(USETL)
        etl.index_code = "H30533"
        etl.cache = Mock()
        snapshot = {"daily": pd.DataFrame(), "securities": pd.DataFrame(), "constituents": pd.DataFrame()}
        legacy = pd.DataFrame([{"trade_date": "2021-01-01", "pe_ratio": 18.0}])
        etl.cache.load_h30533_snapshot.return_value = snapshot
        etl.cache.load_us_pe_daily.return_value = legacy
        etl.cache.load_us_pe_stats.return_value = pd.DataFrame()
        with patch.object(etl, "_load_static_fallback", return_value=pd.DataFrame()):
            result = etl.load_result()

        self.assertEqual(result["pe_data"].iloc[0]["pe_ratio"], 18.0)
        self.assertEqual(result["pe_data"].iloc[0]["source_type"], "historical_backfill")
