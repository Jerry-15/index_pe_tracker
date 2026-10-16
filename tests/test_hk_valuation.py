import unittest

from data_sources.hk import HKDataSource


class HKValuationTests(unittest.TestCase):
    def test_loss_making_stock_uses_ps_and_not_negative_pe(self):
        result = HKDataSource.build_snapshot({
            "price": 10.0,
            "revenue": 100.0,
            "net_profit": -20.0,
            "eps": -0.5,
            "shares": 100.0,
            "data_as_of": "2026-10-15",
        })

        self.assertEqual(result["currency"], "HKD")
        self.assertEqual(result["profit_status"], "loss-making")
        self.assertEqual(result["valuation_metric"], "PS")
        self.assertIsNone(result["pe_ratio"])
        self.assertGreater(result["ps_ratio"], 0)
