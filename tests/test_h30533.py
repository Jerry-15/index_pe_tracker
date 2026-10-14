import unittest
from unittest.mock import patch

import pandas as pd
import requests

from data_sources.h30533 import H30533Source, H30533SourceError
from data_sources.sina import SinaDataSource
from data_sources.us import USDataSource
from config import CHINA_INTL_FUND_CODE, CHINA_INTL_INDEX_CODE


class H30533SourceTests(unittest.TestCase):
    def test_official_payload_must_identify_h30533(self):
        source = H30533Source()

        with self.assertRaises(H30533SourceError) as raised:
            source.parse_official({
                "data": {
                    "indexCode": "000300",
                    "asOfDate": "2026-10-13",
                    "constituents": [],
                }
            })

        self.assertEqual(raised.exception.code, "wrong_index")
        self.assertEqual(raised.exception.details["expected"], CHINA_INTL_INDEX_CODE)

    def test_official_payload_rejects_unrelated_shape_with_structured_error(self):
        source = H30533Source()

        with self.assertRaises(H30533SourceError) as raised:
            source.parse_official({"status": "ok", "data": {"items": []}})

        self.assertEqual(raised.exception.code, "missing_index_identity")
        self.assertIn("expected", raised.exception.details)

    def test_parse_official_normalizes_rows_and_percent_weights(self):
        source = H30533Source()
        payload = {
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"securityCode": "00700", "securityName": "Tencent", "exchange": "HK", "weight": "8.25"},
                    {"securityCode": "BABA", "securityName": "Alibaba", "exchange": "US", "weight": 5},
                ],
            }
        }

        result = source.parse_official(payload)

        self.assertEqual(list(result.columns[:7]), [
            "index_code", "security_code", "name", "exchange", "weight", "as_of", "source_type"
        ])
        self.assertEqual(result["index_code"].tolist(), [CHINA_INTL_INDEX_CODE] * 2)
        self.assertEqual(result["security_code"].tolist(), ["00700.HK", "BABA"])
        self.assertEqual(result["weight"].tolist(), [0.0825, 0.05])
        self.assertEqual(result["source_type"].unique().tolist(), ["csi_official"])
        self.assertEqual(result["as_of"].unique().tolist(), ["2026-10-13"])
        self.assertEqual(list(result.columns), H30533Source.REQUIRED_COLUMNS)
        self.assertIsInstance(result.iloc[0]["raw_metadata"], dict)

    def test_parse_etf_disclosure_is_explicit_fallback(self):
        source = H30533Source()
        payload = {
            "fundCode": CHINA_INTL_FUND_CODE,
            "as_of": "2026/10/10",
            "holdings": [
                {"code": "00700", "name": "Tencent", "market": "HK", "weight_pct": "7.1%"},
            ],
        }

        result = source.parse_etf_disclosure(payload)

        self.assertEqual(result.iloc[0]["source_type"], "etf_disclosure")
        self.assertEqual(result.iloc[0]["as_of"], "2026-10-10")
        self.assertEqual(result.iloc[0]["security_code"], "00700.HK")
        self.assertEqual(result.iloc[0]["weight"], 0.071)
        self.assertEqual(source.last_source_type, "etf_disclosure")

    def test_missing_code_and_weight_are_rejected_with_reasons(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"name": "No code", "exchange": "HK", "weight": 1},
                    {"code": "00700", "name": "No weight", "exchange": "HK"},
                ],
            },
        })

        self.assertTrue(result.empty)
        self.assertEqual(
            [item["reason"] for item in source.rejected_rows],
            ["missing security code", "missing weight"],
        )

    def test_out_of_range_weight_is_rejected(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "00700", "name": "Tencent", "exchange": "HK", "weight": 100.1},
                ],
            },
        })

        self.assertTrue(result.empty)
        self.assertIn("weight out of range", source.rejected_rows[0]["reason"])

    def test_invalid_as_of_is_rejected(self):
        source = H30533Source()

        with self.assertRaises(H30533SourceError) as raised:
            source.parse_official({
                "data": {
                    "indexCode": CHINA_INTL_INDEX_CODE,
                    "asOfDate": "not-a-date",
                    "constituents": [],
                },
            })

        self.assertEqual(raised.exception.code, "invalid_as_of")

    def test_malformed_constituent_array_is_rejected(self):
        source = H30533Source()

        with self.assertRaises(H30533SourceError) as raised:
            source.parse_official({
                "data": {
                    "indexCode": CHINA_INTL_INDEX_CODE,
                    "asOfDate": "2026-10-13",
                    "constituents": {"code": "00700"},
                },
            })

        self.assertEqual(raised.exception.code, "malformed_rows")

    def test_required_schema_is_stable_for_empty_valid_snapshot(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [],
            },
        })

        self.assertEqual(list(result.columns), H30533Source.REQUIRED_COLUMNS)

    def test_malformed_rows_are_rejected_with_reason(self):
        source = H30533Source()
        payload = {
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "00700", "name": "Tencent", "exchange": "HK", "weight": "bad"},
                    {"code": "09988", "name": "Alibaba", "exchange": "HK", "weight": 4},
                ],
            },
        }

        result = source.parse_official(payload)

        self.assertEqual(result["security_code"].tolist(), ["09988.HK"])
        self.assertEqual(len(source.rejected_rows), 1)
        self.assertIn("weight", source.rejected_rows[0]["reason"])

    def test_missing_source_date_is_rejected(self):
        source = H30533Source()

        with self.assertRaises(H30533SourceError):
            source.parse_official({"constituents": [{"code": "00700", "weight": 5}]})

    def test_get_snapshot_marks_etf_fallback_after_official_failure(self):
        calls = []

        def fetcher(url, params):
            calls.append(params)
            if params.get("indexCode"):
                raise RuntimeError("CSI unavailable")
            return {
                "fundCode": CHINA_INTL_FUND_CODE,
                "as_of": "2026-10-10",
                "holdings": [{"code": "00700", "name": "Tencent", "market": "HK", "weight_pct": 7.1}],
            }

        source = H30533Source(fetcher=fetcher, retries=0)
        result = source.get_snapshot()

        self.assertEqual(result.iloc[0]["source_type"], "etf_disclosure")
        self.assertEqual(calls, [{"indexCode": CHINA_INTL_INDEX_CODE}, {"fundCode": CHINA_INTL_FUND_CODE}])

    def test_get_snapshot_deduplicates_and_canonicalizes_issuer_ids(self):
        def fetcher(url, params):
            return {
                "data": {
                    "indexCode": CHINA_INTL_INDEX_CODE,
                    "asOfDate": "2026-10-13",
                    "constituents": [
                        {"code": "09988", "name": "Alibaba HK", "exchange": "HK",
                         "weight": 4, "issuer_id": " alibaba "},
                        {"code": "BABA", "name": "Alibaba ADR", "exchange": "US",
                         "weight": 3, "issuer_id": "ALIBABA"},
                    ],
                },
            }

        source = H30533Source(fetcher=fetcher, retries=0)

        result = source.get_snapshot()

        self.assertEqual(result["security_code"].tolist(), ["09988.HK"])
        self.assertEqual(result["issuer_id"].tolist(), ["ALIBABA"])

    def test_get_snapshot_prefers_ordinary_and_preserves_duplicate_metadata(self):
        source = H30533Source()
        rows = [
            {"code": "BABA", "name": "Alibaba ADR", "exchange": "US", "weight": 3,
             "issuer_id": "ALIBABA", "security_type": "adr", "ads_ratio": 8,
             "diagnostic": "adr row"},
            {"code": "09988", "name": "Alibaba HK", "exchange": "HK", "weight": 4,
             "issuer_id": "ALIBABA", "security_type": "ordinary",
             "diagnostic": "ordinary row"},
        ]
        result = source.deduplicate_issuers(source.parse_official({
            "data": {"indexCode": CHINA_INTL_INDEX_CODE, "asOfDate": "2026-10-13",
                     "constituents": rows}
        }))

        self.assertEqual(result["security_code"].tolist(), ["09988.HK"])
        metadata = result.iloc[0]["raw_metadata"]
        self.assertEqual(len(metadata["duplicate_issuer_rows"]), 2)
        self.assertEqual(
            {row["diagnostic"] for row in metadata["duplicate_issuer_rows"]},
            {"adr row", "ordinary row"},
        )

    def test_invalid_ads_metadata_is_rejected(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "BABA", "name": "Alibaba", "exchange": "US",
                     "weight": 5, "ads_ratio": 0},
                    {"code": "PDD", "name": "PDD", "exchange": "US",
                     "weight": 4, "ads_ratio": "nan"},
                ],
            },
        })

        self.assertTrue(result.empty)
        self.assertEqual(len(source.rejected_rows), 2)
        self.assertTrue(all("ads_ratio" in row["reason"] for row in source.rejected_rows))

    def test_blank_issuer_id_is_rejected(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "BABA", "name": "Alibaba", "exchange": "US",
                     "weight": 5, "issuer_id": "  "},
                ],
            },
        })

        self.assertTrue(result.empty)
        self.assertEqual(source.rejected_rows[0]["reason"], "blank issuer_id")

    def test_get_snapshot_retries_transient_transport_error(self):
        attempts = []

        def fetcher(url, params):
            attempts.append(params)
            if len(attempts) == 1:
                raise requests.ConnectionError("temporary outage")
            return {
                "data": {
                    "indexCode": CHINA_INTL_INDEX_CODE,
                    "asOfDate": "2026-10-13",
                    "constituents": [{"code": "00700", "name": "Tencent", "exchange": "HK", "weight": 7}],
                },
            }

        source = H30533Source(fetcher=fetcher, retries=1)

        result = source.get_snapshot()

        self.assertEqual(len(attempts), 2)
        self.assertEqual(result.iloc[0]["source_type"], "csi_official")

    def test_get_snapshot_preserves_deterministic_source_error(self):
        def fetcher(url, params):
            return {"data": {"indexCode": "000300"}}

        source = H30533Source(fetcher=fetcher, retries=2)

        with self.assertRaises(H30533SourceError) as raised:
            source.get_snapshot()

        self.assertEqual(raised.exception.code, "wrong_index")

    def test_etf_disclosure_must_identify_513050(self):
        source = H30533Source()

        with self.assertRaises(H30533SourceError) as raised:
            source.parse_etf_disclosure({
                "fundCode": "000001",
                "as_of": "2026-10-10",
                "holdings": [],
            })

        self.assertEqual(raised.exception.code, "wrong_fund")

    def test_normalize_code_rejects_whitespace_and_invalid_values(self):
        source = H30533Source()

        for code in (" 00700", "007 00", "not a code", ""):
            with self.assertRaises(ValueError):
                source.normalize_code(code, "HK")

    def test_normalize_code_handles_exchange_and_preserves_us_symbols(self):
        source = H30533Source()

        self.assertEqual(source.normalize_code("00700", "HK"), "00700.HK")
        self.assertEqual(source.normalize_code("600000", "SH"), "600000.SH")
        self.assertEqual(source.normalize_code("BABA", "US"), "BABA")

    def test_duplicate_issuer_fixture_keeps_official_line_once(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "09988", "name": "Alibaba HK", "exchange": "HK",
                     "weight": 4, "issuer_id": "ALIBABA", "security_type": "ordinary"},
                    {"code": "BABA", "name": "Alibaba ADR", "exchange": "US",
                     "weight": 3, "issuer_id": "ALIBABA", "security_type": "adr",
                     "ads_ratio": 8},
                    {"code": "09988", "name": "Alibaba HK", "exchange": "HK",
                     "weight": 4, "issuer_id": "ALIBABA", "security_type": "ordinary"},
                ],
            }
        })

        self.assertEqual(result["security_code"].tolist(), ["09988.HK", "BABA"])
        self.assertEqual(result["issuer_id"].tolist(), ["ALIBABA", "ALIBABA"])
        self.assertEqual(result["weight"].tolist(), [0.04, 0.03])
        self.assertTrue(pd.isna(result.iloc[0]["ads_ratio"]))
        self.assertEqual(result.iloc[1]["ads_ratio"], 8)
        self.assertEqual(result["is_duplicate_issuer"].tolist(), [False, True])
        self.assertEqual(source.deduplicate_issuers(result)["security_code"].tolist(), ["09988.HK"])

    def test_identity_is_not_inferred_from_similar_names(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "09988", "name": "Alibaba", "exchange": "HK", "weight": 4},
                    {"code": "BABA", "name": "Alibaba", "exchange": "US", "weight": 3},
                ],
            }
        })

        self.assertTrue(result["issuer_id"].isna().all())
        self.assertFalse(result["is_duplicate_issuer"].any())

    def test_common_input_reports_missing_shares_and_profit_without_zero(self):
        source = USDataSource()
        record = source.build_input_record(
            "BABA", price=80, currency="USD", shares_or_market_cap=None,
            profit_ttm=None, financial_as_of=None, source="fixture",
        )

        self.assertEqual(record["price"], 80)
        self.assertIsNone(record["shares_or_market_cap"])
        self.assertIsNone(record["profit_ttm"])
        self.assertIn("shares_or_market_cap", record["validity_reason"])
        self.assertIn("profit_ttm", record["validity_reason"])

    def test_current_pe_uses_common_record_without_synthetic_shares(self):
        source = USDataSource()
        quotes = pd.DataFrame([{"close": 80.0}])
        financials = pd.DataFrame([{
            "EPS_TTM": 2.0,
            "CURRENCY": "USD",
            "REPORT_DATE": "2026-09-30",
        }])

        with patch.object(source, "get_daily_quotes", return_value=quotes), \
             patch.object(source, "get_financials", return_value=financials):
            record = source.get_current_pe("BABA")

        self.assertEqual(record["price"], 80.0)
        self.assertEqual(record["currency"], "USD")
        self.assertIsNone(record["shares_or_market_cap"])
        self.assertIn("missing shares_or_market_cap", record["validity_reason"])
        self.assertIsNone(record["profit_ttm"])
        self.assertEqual(record["eps_ttm"], 2.0)
        self.assertEqual(record["financial_as_of"], "2026-09-30")
        self.assertEqual(record["source"], "akshare")
        self.assertEqual(record["ads_ratio"], 8.0)
        self.assertNotIn("pe", record)

    def test_current_pe_returns_structured_diagnostic_for_missing_inputs(self):
        source = USDataSource()

        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame()), \
             patch.object(source, "get_financials", return_value=pd.DataFrame()):
            record = source.get_current_pe("BABA")

        self.assertIsNone(record["price"])
        self.assertIsNone(record["profit_ttm"])
        self.assertIn("missing price", record["validity_reason"])
        self.assertIn("missing profit_ttm", record["validity_reason"])
        self.assertIn("missing financial_as_of", record["validity_reason"])
        self.assertEqual(record["ads_ratio"], 8.0)
        self.assertNotIn("pe", record)

    def test_current_pe_reports_invalid_ads_ratio(self):
        source = USDataSource()
        with patch.object(source, "ADS_RATIO", {"BABA": 0}):
            with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
                 patch.object(source, "get_financials", return_value=pd.DataFrame([{
                     "EPS_TTM": 2.0,
                     "CURRENCY": "USD",
                     "REPORT_DATE": "2026-09-30",
                 }])):
                record = source.get_current_pe("BABA")

        self.assertIn("invalid ads_ratio", record["validity_reason"])
        self.assertNotIn("pe", record)

    def test_current_pe_unknown_us_inputs_are_missing_not_defaulted(self):
        source = USDataSource()
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
             patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0, "REPORT_DATE": "2026-09-30",
             }])):
            record = source.get_current_pe("UNKNOWN")

        self.assertIsNone(record["ads_ratio"])
        self.assertIsNone(record["currency"])
        self.assertIsNone(record["profit_ttm"])
        self.assertEqual(record["eps_ttm"], 2.0)
        self.assertIn("missing currency", record["validity_reason"])
        self.assertIn("missing ads_ratio", record["validity_reason"])

    def test_current_pe_uses_profit_field_for_common_record(self):
        source = USDataSource()
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
             patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0,
                 "PROFIT_TTM": 123.45,
                 "CURRENCY": "USD",
                 "REPORT_DATE": "2026-09-30",
             }])):
            record = source.get_current_pe("BABA")

        self.assertEqual(record["profit_ttm"], 123.45)
        self.assertEqual(record["eps_ttm"], 2.0)

    def test_current_pe_rejects_explicit_unsupported_currency(self):
        source = USDataSource()
        for currency in ("EUR", "GBP", "unknown"):
            with self.subTest(currency=currency), \
                 patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
                 patch.object(source, "get_financials", return_value=pd.DataFrame([{
                     "EPS_TTM": 2.0,
                     "CURRENCY": currency,
                     "REPORT_DATE": "2026-09-30",
                 }])):
                record = source.get_current_pe("BABA")

            self.assertIsNone(record["currency"])
            self.assertIn("unsupported currency", record["validity_reason"])
            self.assertNotIn("pe", record)

    def test_current_pe_keeps_missing_currency_missing(self):
        source = USDataSource()
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
             patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0,
                 "REPORT_DATE": "2026-09-30",
             }])):
            record = source.get_current_pe("BABA")

        self.assertIsNone(record["currency"])
        self.assertIn("missing currency", record["validity_reason"])
        self.assertNotIn("pe", record)

    def test_current_pe_rejects_hkd_without_verified_hkd_usd_rate(self):
        source = USDataSource()
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
             patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0,
                 "CURRENCY": "HKD",
                 "REPORT_DATE": "2026-09-30",
             }])):
            record = source.get_current_pe("BABA")

        self.assertEqual(record["currency"], "HKD")
        self.assertIn("HKD/USD conversion unavailable", record["validity_reason"])
        self.assertNotIn("pe", record)

    def test_current_pe_reports_non_positive_profit(self):
        source = USDataSource()
        for profit in (0, -1):
            with self.subTest(profit=profit), \
                 patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
                 patch.object(source, "get_financials", return_value=pd.DataFrame([{
                     "EPS_TTM": 2.0,
                     "PROFIT_TTM": profit,
                     "CURRENCY": "USD",
                     "REPORT_DATE": "2026-09-30",
                 }])):
                record = source.get_current_pe("BABA")

            self.assertIn("non_positive profit_ttm", record["validity_reason"])
            self.assertNotIn("pe", record)

    def test_current_pe_does_not_calculate_with_non_positive_profit_when_inputs_are_complete(self):
        source = USDataSource()
        complete_record = {
            "code": "BABA", "price": 80.0, "currency": "USD",
            "shares_or_market_cap": 1.0, "profit_ttm": -1.0,
            "financial_as_of": "2026-09-30", "source": "fixture",
            "ads_ratio": 8.0, "validity_reason": "non_positive profit_ttm",
        }
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
             patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0, "PROFIT_TTM": -1.0, "CURRENCY": "USD",
                 "REPORT_DATE": "2026-09-30",
             }])), \
             patch.object(source, "build_input_record", return_value=complete_record):
            record = source.get_current_pe("BABA")

        self.assertNotIn("pe", record)

    def test_common_input_reports_invalid_numeric_values(self):
        cases = (
            ("price", 0), ("price", float("inf")),
            ("shares_or_market_cap", 0), ("shares_or_market_cap", float("nan")),
            ("profit_ttm", 0), ("profit_ttm", -1),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                values = {
                    "price": 80.0, "currency": "USD", "shares_or_market_cap": 1.0,
                    "profit_ttm": 1.0, "financial_as_of": "2026-09-30", "source": "fixture",
                }
                values[field] = value
                record = USDataSource.build_input_record("BABA", ads_ratio=8, **values)

            self.assertIn(f"invalid {field}", record["validity_reason"])

    def test_common_input_reports_non_numeric_ads_ratio_without_raising(self):
        for value in ("not-a-number", object()):
            with self.subTest(value=value):
                record = USDataSource.build_input_record(
                    "BABA", price=80, currency="USD", shares_or_market_cap=1,
                    profit_ttm=1, financial_as_of="2026-09-30", source="fixture",
                    ads_ratio=value,
                )

            self.assertEqual(record["ads_ratio"], value)
            self.assertIn("invalid ads_ratio", record["validity_reason"])

    def test_common_input_reports_none_and_non_finite_numeric_values(self):
        for value in (None, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                record = USDataSource.build_input_record(
                    "BABA", price=value, currency="USD", shares_or_market_cap=1,
                    profit_ttm=1, financial_as_of="2026-09-30", source="fixture", ads_ratio=8,
                )
            self.assertIn("missing price" if value is None else "invalid price", record["validity_reason"])

    def test_daily_quotes_preserve_unavailable_outstanding_shares(self):
        source = USDataSource()
        with patch("data_sources.us.ak.stock_us_daily", return_value=pd.DataFrame([
            {"date": "2026-10-13", "close": 80.0},
        ])):
            quotes = source.get_daily_quotes("UNKNOWN")

        self.assertTrue(pd.isna(quotes.loc[0, "outstanding_share"]))

    def test_sina_daily_quotes_preserve_missing_outstanding_shares(self):
        source = SinaDataSource()
        with patch("data_sources.sina.ak.stock_zh_a_daily", return_value=pd.DataFrame([
            {"date": "2026-10-13", "close": 10.0, "outstanding_share": 0.0},
        ])):
            quotes = source.get_daily_quotes("600000")

        self.assertTrue(pd.isna(quotes.loc[0, "outstanding_share"]))


if __name__ == "__main__":
    unittest.main()
