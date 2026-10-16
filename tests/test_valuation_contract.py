import unittest

import pandas as pd

from etl.valuation import compute_statistics, normalize_result


class ValuationContractTests(unittest.TestCase):
    def test_normalize_result_exposes_common_index_fields(self):
        frame = normalize_result(pd.DataFrame([{
            "trade_date": "2026-10-14",
            "pe_ratio": 12.0,
            "stock_count": 8,
            "weight_coverage": 0.8,
            "source_type": "csi_official",
        }]), index_code="H30533", constituent_count=10)

        self.assertEqual(frame.iloc[0]["index_code"], "H30533")
        self.assertEqual(frame.iloc[0]["valid_stock_count"], 8)
        self.assertEqual(frame.iloc[0]["coverage_ratio"], 0.8)
        self.assertEqual(frame.iloc[0]["data_source"], "csi_official")

    def test_statistics_ignore_null_pe_values(self):
        frame = normalize_result(pd.DataFrame([
            {"trade_date": "2026-10-13", "pe_ratio": 10.0},
            {"trade_date": "2026-10-14", "pe_ratio": None},
            {"trade_date": "2026-10-15", "pe_ratio": 20.0},
        ]), index_code="000300")

        result = compute_statistics(frame)

        self.assertTrue(pd.isna(result.iloc[1]["pe_percentile"]))
        self.assertEqual(result.iloc[0]["pe_percentile"], 50.0)
        self.assertEqual(result.iloc[2]["pe_percentile"], 100.0)
