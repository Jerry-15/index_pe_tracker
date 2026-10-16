import unittest

from data_models.security import SecurityRecord


class SecurityRecordTests(unittest.TestCase):
    def test_roundtrip_preserves_normalized_fields(self):
        record = SecurityRecord.from_mapping({
            "security_code": "PDD",
            "isin": "US7223041028",
            "market_cap": 100.0,
            "market_cap_method": "reported_market_cap",
            "quality_level": "B",
        })
        self.assertEqual(record.to_dict()["isin"], "US7223041028")

    def test_rejects_invalid_market_cap_method_and_nonfinite_values(self):
        with self.assertRaises(ValueError):
            SecurityRecord("PDD", market_cap_method="guessed")
        with self.assertRaises(ValueError):
            SecurityRecord("PDD", market_cap=float("nan"))

    def test_missing_values_remain_none_and_reasons_are_split(self):
        record = SecurityRecord("PDD", validity_reason="stale_financial;missing_ads_ratio")
        self.assertIsNone(record.market_cap)
        self.assertEqual(record.quality_reasons, ["stale_financial", "missing_ads_ratio"])


if __name__ == "__main__":
    unittest.main()
