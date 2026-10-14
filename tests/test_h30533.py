import unittest
from unittest import mock

import pandas as pd

from data_sources.h30533 import H30533Source, H30533SourceError
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

    def test_etf_disclosure_requires_matching_fund_identity(self):
        source = H30533Source()

        with self.assertRaises(H30533SourceError) as raised:
            source.parse_etf_disclosure({
                "fundCode": "000001",
                "as_of": "2026-10-10",
                "holdings": [],
            })

        self.assertEqual(raised.exception.code, "wrong_fund")
        self.assertEqual(raised.exception.details["expected"], CHINA_INTL_FUND_CODE)

        with self.assertRaises(H30533SourceError) as raised:
            source.parse_etf_disclosure({
                "as_of": "2026-10-10",
                "holdings": [],
            })

        self.assertEqual(raised.exception.code, "missing_fund_identity")

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

    def test_get_snapshot_falls_back_after_official_wrong_index_payload(self):
        official_payload = {
            "data": {
                "indexCode": "000300",
                "asOfDate": "2026-10-13",
                "constituents": [],
            }
        }
        etf_payload = {
            "fundCode": CHINA_INTL_FUND_CODE,
            "as_of": "2026-10-10",
            "holdings": [
                {"code": "00700", "name": "Tencent", "market": "HK", "weight_pct": 7.1},
            ],
        }
        calls = []

        def fetcher(url, params):
            calls.append((url, params))
            return official_payload if params.get("indexCode") else etf_payload

        source = H30533Source(
            fetcher=fetcher,
            retries=2,
            official_url="https://official.test",
            etf_url="https://etf.test",
        )

        result = source.get_snapshot()

        self.assertEqual(result.iloc[0]["source_type"], "etf_disclosure")
        self.assertEqual(calls, [
            ("https://official.test", {"indexCode": CHINA_INTL_INDEX_CODE}),
            ("https://etf.test", {"fundCode": CHINA_INTL_FUND_CODE}),
        ])

    def test_normalize_code_handles_exchange_and_preserves_us_symbols(self):
        source = H30533Source()

        self.assertEqual(source.normalize_code("00700", "HK"), "00700.HK")
        self.assertEqual(source.normalize_code("600000", "SH"), "600000.SH")
        self.assertEqual(source.normalize_code("BABA", "US"), "BABA")

    def test_invalid_codes_and_unknown_exchanges_are_rejected(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "   ", "name": "blank", "exchange": "HK", "weight": 1},
                    {"code": "00700", "name": "unknown", "exchange": "OTC", "weight": 1},
                    {"code": "not valid!", "name": "bad", "exchange": "US", "weight": 1},
                ],
            },
        })

        self.assertTrue(result.empty)
        self.assertEqual(len(source.rejected_rows), 3)

    def test_deterministic_parser_failure_is_not_retried(self):
        calls = []

        def fetcher(url, params):
            calls.append(params)
            return {"data": {"indexCode": "000300", "asOfDate": "2026-10-13", "constituents": []}}

        source = H30533Source(fetcher=fetcher, retries=2)

        with self.assertRaises(H30533SourceError) as raised:
            source.get_snapshot()

        self.assertEqual(raised.exception.code, "all_sources_rejected")
        self.assertEqual(calls, [{"indexCode": CHINA_INTL_INDEX_CODE}, {"fundCode": CHINA_INTL_FUND_CODE}])
        self.assertEqual(raised.exception.details["errors"][0]["code"], "wrong_index")
        self.assertEqual(raised.exception.details["errors"][1]["code"], "missing_fund_identity")

    def test_urls_are_injected_and_environment_is_read_per_instance(self):
        first = H30533Source(official_url="https://one", etf_url="https://two")
        self.assertEqual(first.official_url, "https://one")
        self.assertEqual(first.etf_url, "https://two")

        with mock.patch.dict("os.environ", {
            "H30533_OFFICIAL_URL": "https://env-one",
            "H30533_ETF_URL": "https://env-two",
        }):
            second = H30533Source()

        self.assertEqual(second.official_url, "https://env-one")
        self.assertEqual(second.etf_url, "https://env-two")

    def test_constructor_urls_are_passed_to_fetcher(self):
        calls = []

        def fetcher(url, params):
            calls.append(url)
            if params.get("indexCode"):
                return {"data": {"indexCode": "000300"}}
            return {
                "fundCode": CHINA_INTL_FUND_CODE,
                "as_of": "2026-10-10",
                "holdings": [{"code": "00700", "name": "Tencent", "market": "HK", "weight_pct": 7.1}],
            }

        source = H30533Source(
            fetcher=fetcher,
            retries=0,
            official_url="https://injected-official.test",
            etf_url="https://injected-etf.test",
        )

        source.get_snapshot()

        self.assertEqual(calls, ["https://injected-official.test", "https://injected-etf.test"])

    def test_issuer_identity_is_canonicalized_and_blank_source_identity_is_missing(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": " h30533 ",
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "09988", "name": "Alibaba", "exchange": "HK", "weight": 4,
                     "issuer_id": "  AliBaba  "},
                ],
            }
        })

        self.assertEqual(result.iloc[0]["issuer_id"], "ALIBABA")

        with self.assertRaises(H30533SourceError) as raised:
            source.parse_official({
                "data": {"indexCode": "   ", "asOfDate": "2026-10-13", "constituents": []}
            })
        self.assertEqual(raised.exception.code, "missing_index_identity")

    def test_blank_issuer_identity_does_not_create_duplicate(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "09988", "name": "Alibaba", "exchange": "HK", "weight": 4,
                     "issuer_id": "   "},
                    {"code": "BABA", "name": "Alibaba ADR", "exchange": "US", "weight": 3},
                ],
            }
        })

        self.assertTrue(result["issuer_id"].isna().all())
        self.assertFalse(result["is_duplicate_issuer"].any())

    def test_invalid_ads_ratio_is_rejected(self):
        source = H30533Source()
        result = source.parse_official({
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "BABA", "name": "Alibaba", "exchange": "US", "weight": 4,
                     "ads_ratio": 0},
                    {"code": "PDD", "name": "PDD", "exchange": "US", "weight": 3,
                     "ads_ratio": float("nan")},
                ],
            }
        })

        self.assertTrue(result.empty)
        self.assertEqual(len(source.rejected_rows), 2)
        self.assertTrue(all("ads ratio" in item["reason"] for item in source.rejected_rows))

    def test_snapshot_deduplicates_and_prefers_ordinary_security(self):
        source = H30533Source()
        source.fetcher = lambda url, params: {
            "data": {
                "indexCode": CHINA_INTL_INDEX_CODE,
                "asOfDate": "2026-10-13",
                "constituents": [
                    {"code": "BABA", "name": "Alibaba ADR", "exchange": "US", "weight": 3,
                     "issuer_id": "alibaba", "security_type": "ADR", "ads_ratio": 8},
                    {"code": "09988", "name": "Alibaba", "exchange": "HK", "weight": 4,
                     "issuer_id": " ALIBABA ", "security_type": "ordinary"},
                ],
            }
        }

        result = source.get_snapshot()

        self.assertEqual(result["security_code"].tolist(), ["09988.HK"])
        self.assertEqual(result["weight"].tolist(), [0.04])
        self.assertEqual(result.iloc[0]["security_type"], "ordinary")

    def test_common_input_defaults_ads_ratio_and_reports_all_missing_values(self):
        record = USDataSource.build_input_record(
            " BABA ", price=float("nan"), currency=pd.NA,
            shares_or_market_cap=pd.NA, profit_ttm="", financial_as_of=None,
            source="",
        )

        self.assertEqual(record["ads_ratio"], USDataSource.ADS_RATIO["BABA"])
        self.assertEqual(record["validity_reason"],
                         "missing price; missing currency; missing shares_or_market_cap; "
                         "missing profit_ttm; missing financial_as_of; missing source")

    def test_common_input_rejects_non_positive_or_non_finite_ads_ratio(self):
        for ratio in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                USDataSource.build_input_record(
                    "PDD", 80, "USD", 1, 1, "2026-10-13", "fixture", ads_ratio=ratio
                )

    def test_current_pe_reports_malformed_configured_ads_ratio(self):
        source = USDataSource()
        with mock.patch.object(source, "ADS_RATIO", {"BABA": "not-a-number"}), \
             mock.patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
             mock.patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0,
                 "CURRENCY": "USD",
                 "REPORT_DATE": "2026-09-30",
             }])):
            record = source.get_current_pe("BABA")

        self.assertIsNotNone(record)
        self.assertIn("invalid ads_ratio", record["validity_reason"])
        self.assertNotIn("pe", record)

    def test_common_input_reports_unsupported_currency(self):
        record = USDataSource.build_input_record(
            "BABA", price=80, currency="EUR", shares_or_market_cap=1,
            profit_ttm=1, financial_as_of="2026-09-30", source="fixture", ads_ratio=8,
        )

        self.assertEqual(record["currency"], "EUR")
        self.assertIn("unsupported currency: EUR", record["validity_reason"])


if __name__ == "__main__":
    unittest.main()
