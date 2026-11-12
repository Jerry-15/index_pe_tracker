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
        values = [10.0 + (index % 5) for index in range(200)]
        values[150] = None
        frame = normalize_result(pd.DataFrame({
            "trade_date": pd.date_range("2020-01-01", periods=200, freq="B").strftime("%Y-%m-%d"),
            "pe_ratio": values,
        }), index_code="000300")

        result = compute_statistics(frame)

        # A missing PE never receives a percentile of its own.
        self.assertTrue(pd.isna(result.iloc[150]["pe_percentile"]))
        # Later rows still compute normally instead of being polluted by a zero fill.
        self.assertFalse(pd.isna(result.iloc[-1]["pe_percentile"]))
        valid = result["pe_percentile"].dropna()
        self.assertTrue(((valid >= 0) & (valid <= 100)).all())
