import unittest
from unittest import mock
from unittest.mock import MagicMock, patch
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

from data_sources.h30533 import H30533Source, H30533SourceError
from data_sources.us import USDataSource
from config import CHINA_INTL_FUND_CODE, CHINA_INTL_INDEX_CODE
from cache.db import CacheDB
from etl.pipeline_us import USETL


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

    def test_source_metadata_is_reset_between_attempts_and_etf_gets_its_own_provenance(self):
        payload = {
            "fundCode": CHINA_INTL_FUND_CODE,
            "as_of": "2026-10-10",
            "holdings": [{"code": "00700", "name": "Tencent", "market": "HK", "weight_pct": 7.1}],
        }
        seen = []

        def fetcher(url, params):
            seen.append((url, dict(source.last_source_meta)))
            if params.get("indexCode"):
                source.last_source_meta = {"source_url": "https://stale.test", "fetched_at": "stale"}
                raise RuntimeError("CSI unavailable")
            return payload

        source = H30533Source(fetcher=fetcher, retries=0,
                              official_url="https://official.test", etf_url="https://etf.test")
        result = source.get_snapshot()

        self.assertEqual(seen[0][1], {})
        self.assertEqual(seen[1][1], {})
        self.assertEqual(result.iloc[0]["source_url"], "https://etf.test")
        self.assertNotEqual(result.iloc[0]["fetched_at"], "stale")

    def test_source_errors_include_attempt_provenance(self):
        source = H30533Source(fetcher=lambda url, params: (_ for _ in ()).throw(RuntimeError("down")),
                              retries=0, official_url="https://official.test", etf_url="https://etf.test")

        with self.assertRaises(H30533SourceError) as raised:
            source.get_snapshot()

        error = raised.exception.details["errors"][0]
        self.assertEqual(error["source_url"], "https://official.test")
        self.assertTrue(error["fetched_at"])

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

    def test_current_pe_extracts_verified_shares_and_profit_from_source_records(self):
        source = USDataSource()
        with mock.patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{
             "close": 80.0,
             "market_cap": None,
             "shares_outstanding": 10.0,
         }])), mock.patch.object(source, "get_current_pe", return_value={
             "verified_shares": 10.0, "profit_ttm": 80.0,
         }):
            record = source.get_current_pe("BABA")

        self.assertEqual(record["verified_shares"], 10.0)
        self.assertEqual(record["profit_ttm"], 80.0)
        self.assertNotIn("market_cap", record)

    def test_current_pe_source_fixture_shape(self):
        source = USDataSource()
        with mock.patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{
             "close": 80.0,
         }])), mock.patch.object(source, "get_financials", return_value=pd.DataFrame([{
             "EPS_TTM": 2.0,
            "CURRENCY": "USD",
            "REPORT_DATE": "2026-09-30",
            "NET_PROFIT_TTM": 80.0,
         }])):
            record = source.get_current_pe("BABA")

    def test_h30533_snapshot_records_produce_pe_and_count_negative_profit(self):
        etl = USETL(cache=MagicMock())
        etl.h30533_source = MagicMock()
        etl.source = MagicMock()
        etl.h30533_source.get_snapshot.return_value = pd.DataFrame([
            {"security_code": "BABA", "name": "Alibaba", "weight": 0.8,
             "source_type": "csi_official", "as_of": "2026-10-13"},
            {"security_code": "PDD", "name": "PDD", "weight": 0.2,
             "source_type": "csi_official", "as_of": "2026-10-13"},
        ])
        etl.source.get_current_pe.side_effect = [
            {"price": 80.0, "verified_shares": 10.0, "profit_ttm": 80.0},
            {"price": 50.0, "market_cap": 500.0, "profit_ttm": -5.0},
        ]

        with patch.object(etl, "_recompute_us_stats"):
            result = etl.run()

        self.assertEqual(result.iloc[0]["pe_ratio"], 10.0)
        self.assertEqual(result.iloc[0]["earnings_yield"], 0.1)
        self.assertEqual(result.iloc[0]["negative_profit_count"], 1)
        self.assertEqual(result.iloc[0]["index_code"], CHINA_INTL_INDEX_CODE)
        self.assertEqual(result.iloc[0]["source_type"], "csi_official")
        saved = etl.cache.save_h30533_snapshot.call_args.args
        self.assertEqual(saved[0], CHINA_INTL_INDEX_CODE)
        self.assertEqual(saved[1]["constituent_as_of"].tolist(), ["2026-10-13", "2026-10-13"])
        self.assertEqual(saved[2].iloc[0]["constituent_as_of"], "2026-10-13")
        self.assertEqual(saved[3]["source_date"].tolist(), ["2026-10-13", "2026-10-13"])
        etl.cache.save_us_pe_snapshot.assert_not_called()
        etl.cache.save_us_stock_pe.assert_not_called()

    def test_h30533_all_invalid_rows_persist_diagnostics_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory, patch("etl.pipeline_us.API_BATCH_DELAY", 0):
            cache = CacheDB(Path(directory) / "cache.db")
            etl = USETL(cache=cache)
            etl.h30533_source = MagicMock()
            etl.source = MagicMock()
            etl.h30533_source.get_snapshot.return_value = pd.DataFrame([
                {"security_code": "BABA", "name": "Alibaba", "weight": 0.6,
                 "source_type": "csi_official", "as_of": "2026-10-13"},
                {"security_code": "PDD", "name": "PDD", "weight": 0.4,
                 "source_type": "csi_official", "as_of": "2026-10-13"},
            ])
            etl.source.get_current_pe.side_effect = [
                {"price": 80.0, "validity_reason": "missing profit_ttm"},
                {"price": 50.0, "profit_ttm": -5.0, "validity_reason": "negative profit"},
            ]

            result = etl.run()
            loaded = etl.load_result()

            self.assertIsNone(result.iloc[0]["index_pe"])
            self.assertEqual(result.iloc[0]["valid_count"], 0)
            self.assertEqual(result.iloc[0]["missing_count"], 2)
            self.assertEqual(result.iloc[0]["validity_reason"], "no valid security valuations")
            self.assertEqual(len(loaded["constituents"]), 2)
            self.assertEqual(len(loaded["stock_pe"]), 2)
            self.assertEqual(loaded["pe_data"].iloc[0]["valid_count"], 0)
            self.assertEqual(
                loaded["stock_pe"]["validity_reason"].tolist(),
                ["missing profit_ttm", "negative profit"],
            )

    def test_h30533_persistence_keeps_canonical_metadata_in_all_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "cache.db")
            constituents = pd.DataFrame([{
                "snapshot_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
                "security_code": "BABA", "name": "Alibaba", "exchange": "US",
                "weight": 0.8, "issuer_id": "ALIBABA", "security_type": "ADR",
                "source_type": "csi_official", "as_of": "2026-10-13",
                "validity_reason": "ok", "extra_constituent": "kept",
            }])
            daily = pd.DataFrame([{
                "trade_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
                "index_pe": 10.0, "earnings_yield": 0.1, "valid_count": 1,
                "stock_count": 1, "weight_coverage": 0.8, "negative_profit_count": 0,
                "missing_count": 0, "source_type": "csi_official",
                "source_date": "2026-10-13", "constituent_as_of": "2026-10-13",
                "constituent_basis": "H30533",
                "validity_reason": "ok", "extra_daily": "kept",
            }])
            securities = pd.DataFrame([{
                "snapshot_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
                "security_code": "BABA", "name": "Alibaba", "issuer_id": "ALIBABA",
                "currency": "USD", "weight": 0.8, "market_cap": 800.0,
                "verified_shares": 10.0, "profit_ttm": 80.0, "earnings_yield": 0.1,
                 "index_pe": 10.0, "financial_as_of": "2026-09-30",
                 "source_date": "2026-10-13", "source_type": "csi_official", "source": "fixture",
                "data_source": "fixture", "validity_reason": "ok",
                "extra_security": "kept",
            }])

            cache.save_h30533_snapshot(CHINA_INTL_INDEX_CODE, constituents, daily, securities)
            result = cache.load_h30533_snapshot(CHINA_INTL_INDEX_CODE)

            self.assertEqual(result["constituents"].iloc[0]["validity_reason"], "ok")
            self.assertEqual(result["daily"].iloc[0]["index_pe"], 10.0)
            self.assertEqual(result["daily"].iloc[0]["valid_count"], 1)
            self.assertEqual(result["securities"].iloc[0]["currency"], "USD")
            self.assertEqual(result["securities"].iloc[0]["data_source"], "fixture")
            self.assertEqual(result["securities"].iloc[0]["index_pe"], 10.0)
            self.assertEqual(result["securities"].iloc[0]["extra_security"], "kept")

    def test_h30533_persists_duplicate_issuer_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "cache.db")
            constituents = pd.DataFrame([
                {"index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA",
                 "weight": 0.6, "as_of": "2026-10-13", "source_type": "fixture",
                 "issuer_id": "ALIBABA", "is_duplicate_issuer": False},
                {"index_code": CHINA_INTL_INDEX_CODE, "security_code": "9988.HK",
                 "weight": 0.4, "as_of": "2026-10-13", "source_type": "fixture",
                 "issuer_id": "ALIBABA", "is_duplicate_issuer": True},
            ])
            daily = pd.DataFrame([{
                "trade_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
                "index_pe": 10, "source_type": "fixture", "source_date": "2026-10-13",
                "constituent_as_of": "2026-10-13",
            }])
            securities = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA",
                "weight": 0.6, "source_type": "fixture", "source_date": "2026-10-13",
            }])

            cache.save_h30533_snapshot(CHINA_INTL_INDEX_CODE, constituents, daily, securities)
            loaded = cache.load_h30533_snapshot(CHINA_INTL_INDEX_CODE)["constituents"]

            self.assertEqual(
                loaded.set_index("security_code")["is_duplicate_issuer"].to_dict(),
                {"BABA": 0, "9988.HK": 1},
            )
            with sqlite3.connect(Path(directory) / "cache.db") as conn:
                columns = {row[1] for row in conn.execute(
                    "PRAGMA table_info(h30533_constituent_snapshot)")}
            self.assertIn("is_duplicate_issuer", columns)

    def test_h30533_rejects_conflicting_constituent_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "cache.db")
            constituents = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA", "weight": 1,
                "as_of": "2026-10-13", "constituent_as_of": "2026-10-12",
                "source_type": "fixture",
            }])
            daily = pd.DataFrame([{
                "trade_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
                "index_pe": 10, "source_type": "fixture", "source_date": "2026-10-13",
                "constituent_as_of": "2026-10-13",
            }])
            securities = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA", "weight": 1,
                "source_type": "fixture", "source_date": "2026-10-13",
            }])

            with self.assertRaisesRegex(ValueError, "constituent_as_of"):
                cache.save_h30533_snapshot(CHINA_INTL_INDEX_CODE, constituents, daily, securities)

    def test_h30533_rejects_inconsistent_security_source_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "cache.db")
            constituents = pd.DataFrame([{
            "index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA", "weight": 0.5,
            "as_of": "2026-10-13", "source_type": "fixture",
            }, {
            "index_code": CHINA_INTL_INDEX_CODE, "security_code": "PDD", "weight": 0.5,
            "as_of": "2026-10-13", "source_type": "fixture",
            }])
            daily = pd.DataFrame([{
            "trade_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
            "index_pe": 10, "source_type": "fixture", "source_date": "2026-10-13",
            "constituent_as_of": "2026-10-13",
            }])
            securities = pd.DataFrame([
            {"index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA", "weight": 0.5,
             "source_type": "fixture", "source_date": "2026-10-13"},
            {"index_code": CHINA_INTL_INDEX_CODE, "security_code": "PDD", "weight": 0.5,
             "source_type": "fixture", "source_date": "2026-10-12"},
            ])

            with self.assertRaisesRegex(ValueError, "security source_date"):
                cache.save_h30533_snapshot(CHINA_INTL_INDEX_CODE, constituents, daily, securities)

    def test_h30533_migrates_every_core_column_and_preserves_legacy_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "cache.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript("""
                    CREATE TABLE h30533_constituent_snapshot (
                        snapshot_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        security_code TEXT NOT NULL, extra_constituent TEXT,
                        PRIMARY KEY (snapshot_date, index_code, security_code)
                    );
                    CREATE TABLE h30533_daily_valuation (
                        trade_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        extra_daily TEXT,
                        PRIMARY KEY (trade_date, index_code)
                    );
                    CREATE TABLE h30533_security_valuation (
                        snapshot_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        security_code TEXT NOT NULL, extra_security TEXT,
                        PRIMARY KEY (snapshot_date, index_code, security_code)
                    );
                """)

            cache = CacheDB(db_path)
            constituents = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA",
                "name": "Alibaba", "exchange": "US", "weight": 0.8,
                "issuer_id": "ALIBABA", "security_type": "ADR", "ads_ratio": 1.0,
                "source_type": "fixture", "as_of": "2026-10-13",
                "constituent_as_of": "2026-10-13", "is_duplicate_issuer": False,
                "raw_metadata": {"fund": "000906"},
                "validity_reason": "ok", "extra_constituent": "kept",
            }])
            daily = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "trade_date": "2026-10-13",
                "pe_ratio": 10.0, "index_pe": 10.0, "earnings_yield": 0.1,
                "total_market_cap": 800.0, "total_profit_ttm": 80.0,
                "stock_count": 1, "valid_count": 1, "weight_coverage": 0.8,
                "negative_profit_count": 0, "missing_count": 0,
                "source_type": "fixture", "source_date": "2026-10-13",
                "constituent_as_of": "2026-10-13", "constituent_basis": "H30533",
                "validity_reason": "ok", "updated_at": 123.0, "extra_daily": "kept",
            }])
            securities = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA",
                "name": "Alibaba", "issuer_id": "ALIBABA", "weight": 0.8,
                "price": 80.0, "currency": "USD", "market_cap": 800.0,
                "verified_shares": 10.0, "profit_ttm": 80.0, "earnings_yield": 0.1,
                "index_pe": 10.0, "pe": 10.0, "financial_as_of": "2026-09-30",
                "source_date": "2026-10-13", "source_type": "fixture",
                "source": "fixture", "data_source": "fixture",
                "validity_reason": "ok", "extra_security": "kept",
            }])

            cache.save_h30533_snapshot(CHINA_INTL_INDEX_CODE, constituents, daily, securities)
            result = cache.load_h30533_snapshot(CHINA_INTL_INDEX_CODE)

            self.assertEqual(result["constituents"].iloc[0]["constituent_as_of"], "2026-10-13")
            self.assertEqual(result["constituents"].iloc[0]["is_duplicate_issuer"], 0)
            self.assertEqual(result["constituents"].iloc[0]["raw_metadata"], {"fund": "000906"})
            self.assertEqual(result["constituents"].iloc[0]["extra_constituent"], "kept")
            self.assertEqual(result["daily"].iloc[0]["index_pe"], 10.0)
            self.assertEqual(result["daily"].iloc[0]["valid_count"], 1)
            self.assertEqual(result["daily"].iloc[0]["constituent_as_of"], "2026-10-13")
            self.assertEqual(result["daily"].iloc[0]["extra_daily"], "kept")
            self.assertEqual(result["securities"].iloc[0]["currency"], "USD")
            self.assertEqual(result["securities"].iloc[0]["index_pe"], 10.0)
            self.assertEqual(result["securities"].iloc[0]["source"], "fixture")
            self.assertEqual(result["securities"].iloc[0]["data_source"], "fixture")
            self.assertEqual(result["securities"].iloc[0]["extra_security"], "kept")

            with sqlite3.connect(db_path) as conn:
                expected_extras = {
                    "h30533_constituent_snapshot": "extra_constituent",
                    "h30533_daily_valuation": "extra_daily",
                    "h30533_security_valuation": "extra_security",
                }
                for table, extra_column in expected_extras.items():
                    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                    self.assertIn(extra_column, columns)

    def test_h30533_atomic_replacement_rejects_mismatched_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "cache.db")
            constituents = pd.DataFrame([{"snapshot_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
                                          "security_code": "BABA",
                                          "as_of": "2026-10-13", "source_type": "fixture",
                                          "weight": 1, "validity_reason": "ok"}])
            daily = pd.DataFrame([{"trade_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
                                   "index_pe": 10, "source_date": "2026-10-13",
                                   "constituent_as_of": "2026-10-13",
                                   "source_type": "fixture", "validity_reason": "ok"}])
            securities = pd.DataFrame([{"snapshot_date": "2026-10-13", "index_code": CHINA_INTL_INDEX_CODE,
                                        "security_code": "BABA",
                                        "source_date": "2026-10-13", "source_type": "fixture",
                                        "weight": 1, "validity_reason": "ok"}])
            cache.save_h30533_snapshot(CHINA_INTL_INDEX_CODE, constituents, daily, securities)
            daily.loc[0, "constituent_as_of"] = "2026-10-12"

            with self.assertRaises(ValueError):
                cache.save_h30533_snapshot(CHINA_INTL_INDEX_CODE, constituents, daily, securities)

            self.assertEqual(cache.load_h30533_snapshot(CHINA_INTL_INDEX_CODE)["daily"].iloc[0]["source_date"],
                             "2026-10-13")

    def test_h30533_allows_independent_source_dates_and_decodes_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "cache.db")
            constituents = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA", "weight": 1,
                "as_of": "2026-10-10", "source_type": "etf_disclosure",
                "raw_metadata": {"fund": "000906"},
            }])
            daily = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "trade_date": "2026-10-13", "index_pe": 10,
                "source_type": "calculated", "source_date": "2026-10-13",
                "constituent_as_of": "2026-10-10",
            }])
            securities = pd.DataFrame([{
                "index_code": CHINA_INTL_INDEX_CODE, "security_code": "BABA", "weight": 1,
                "source_type": "market_data", "source_date": "2026-10-12",
            }])

            cache.save_h30533_snapshot(CHINA_INTL_INDEX_CODE, constituents, daily, securities)
            loaded = cache.load_h30533_snapshot(CHINA_INTL_INDEX_CODE)

            self.assertEqual(loaded["constituents"].iloc[0]["raw_metadata"], {"fund": "000906"})
            self.assertEqual(loaded["daily"].iloc[0]["trade_date"], "2026-10-13")

    def test_h30533_daily_can_be_saved_to_fresh_database(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "cache.db")
            cache.save_h30533_daily(CHINA_INTL_INDEX_CODE, pd.DataFrame([{
                "trade_date": "2026-10-13", "pe_ratio": 10, "source_type": "calculated",
                "source_date": "2026-10-13", "constituent_as_of": "2026-10-10",
            }]))

            daily = cache.load_h30533_snapshot(CHINA_INTL_INDEX_CODE)["daily"]
            self.assertEqual(daily.iloc[0]["pe_ratio"], 10)

    def test_usetl_load_result_reads_h30533_tables_with_legacy_alias(self):
        cache = MagicMock()
        cache.load_h30533_snapshot.return_value = {
            "constituents": pd.DataFrame([{"security_code": "BABA"}]),
            "daily": pd.DataFrame([{"trade_date": "2026-10-13", "pe_ratio": 10}]),
            "securities": pd.DataFrame([{"security_code": "BABA", "pe": 10}]),
        }
        cache.get_us_last_updated.return_value = "2026-10-13"
        etl = USETL(cache=cache)

        result = etl.load_result()

        cache.load_h30533_snapshot.assert_called_once_with(CHINA_INTL_INDEX_CODE)
        cache.load_us_pe_daily.assert_not_called()
        cache.load_us_pe_stats.assert_not_called()
        cache.load_us_stock_pe.assert_not_called()
        self.assertEqual(result["stock_pe"].iloc[0]["code"], "BABA")
        self.assertEqual(result["last_updated"], "2026-10-13")

    def test_common_input_reports_unsupported_currency(self):
        record = USDataSource.build_input_record(
            "BABA", price=80, currency="EUR", shares_or_market_cap=1,
            profit_ttm=1, financial_as_of="2026-09-30", source="fixture", ads_ratio=8,
        )

        self.assertEqual(record["currency"], "EUR")
        self.assertIn("unsupported currency: EUR", record["validity_reason"])

    def test_current_pe_excludes_pe_when_quotes_disagree(self):
        source = USDataSource(secondary_quote_fetcher=lambda code: 100.0)
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
             patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0, "NET_PROFIT_TTM": 80.0, "CURRENCY": "USD",
                 "REPORT_DATE": "2026-09-30",
             }])):
            record = source.get_current_pe("BABA")

        self.assertTrue(record["quote_disagreement"])
        self.assertIn("quote_disagreement", record["validity_reason"])
        self.assertNotIn("pe", record)

    def test_current_pe_direct_reported_market_cap_wins_over_shares(self):
        source = USDataSource()
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{
            "close": 80.0, "market_cap": 900.0, "shares_outstanding": 10.0,
        }])), patch.object(source, "get_financials", return_value=pd.DataFrame([{
            "EPS_TTM": 2.0, "NET_PROFIT_TTM": 80.0, "CURRENCY": "USD",
            "REPORT_DATE": "2026-09-30",
        }])):
            record = source.get_current_pe("BABA")

        self.assertEqual(record["market_cap"], 900.0)
        self.assertEqual(record["market_cap_method"], "reported_market_cap")

    def test_current_pe_uses_documented_financial_stale_diagnostic(self):
        source = USDataSource()
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0}])), \
             patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0, "NET_PROFIT_TTM": 80.0, "CURRENCY": "USD",
                 "REPORT_DATE": "2020-01-01",
             }])):
            record = source.get_current_pe("BABA")

        self.assertIn("financial_stale", record["validity_reason"])
        self.assertNotIn("stale_financial", record["validity_reason"])


if __name__ == "__main__":
    unittest.main()
