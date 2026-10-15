import unittest
import sqlite3
import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import requests

from data_sources.h30533 import H30533Source, H30533SourceError
from data_sources.sina import SinaDataSource
from data_sources.us import USDataSource
from config import CHINA_INTL_FUND_CODE, CHINA_INTL_INDEX_CODE
from etl.pipeline_us import aggregate_h30533, USETL
from cache.db import CacheDB
from scripts import export_static_data


class H30533SourceTests(unittest.TestCase):
    @patch("data_sources.h30533.requests.get")
    def test_request_sends_required_headers_for_etf(self, get):
        response = MagicMock()
        response.json.return_value = {}
        get.return_value = response
        source = H30533Source(timeout=7)

        source._request(source.ETF_URL, {"type": "jjcc"})

        get.assert_called_once_with(
            source.ETF_URL,
            params={"type": "jjcc"},
            timeout=7,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://fundf10.eastmoney.com/ccmx_513050.html",
            },
        )

    def test_static_export_uses_h30533_snapshot_and_exports_metadata(self):
        class FakeCache:
            def load_h30533_snapshot(self, index_code):
                self.loaded_code = index_code
                return {
                    "constituents": pd.DataFrame([{
                        "security_code": "BABA", "name": "Alibaba", "weight": 0.8,
                        "source_type": "csi_official", "constituent_as_of": "2026-10-13",
                    }]),
                    "daily": pd.DataFrame([{
                        "trade_date": "2026-10-14", "pe_ratio": None,
                        "source_type": "csi_official", "source_date": "2026-10-13",
                        "constituent_as_of": "2026-10-13", "constituent_basis": "official",
                        "weight_coverage": 0.8, "stock_count": 0, "valid_count": 0,
                        "negative_profit_count": 1, "missing_count": 0,
                        "invalid_count": 0, "validity_reason": "coverage below minimum",
                    }]),
                    "securities": pd.DataFrame([{
                        "security_code": "BABA", "name": "Alibaba", "weight": 0.8,
                        "price": None, "pe": None, "validity_reason": "missing price",
                    }]),
                }

        with tempfile.TemporaryDirectory() as directory:
            original_out = export_static_data.OUT
            export_static_data.OUT = Path(directory)
            try:
                cache = FakeCache()
                export_static_data._export_china_intl(cache, None)
                index_list = json.loads((Path(directory) / "index_list.json").read_text())
                entries = [item for item in index_list if item["code"] == "H30533"]
                self.assertEqual(len(entries), 1)
                entry = entries[0]
                self.assertEqual(entry["data_file"], "CHINA_INTL.json")
                self.assertEqual(entry["source"], "csi_official")
                self.assertEqual(entry["constituent_as_of"], "2026-10-13")
                self.assertEqual(entry["validity"], "coverage below minimum")
                self.assertEqual(entry["constituent_basis"], "official")
                self.assertEqual(entry["coverage"], 0.8)
                self.assertEqual(entry["stock_diagnostics"][0]["validity_reason"], "missing price")
                rows = json.loads((Path(directory) / entry["data_file"]).read_text())
                self.assertIsNone(rows[0][1])
                self.assertEqual(rows[0][14:18], ["csi_official", "2026-10-13", "2026-10-13", "official"])
            finally:
                export_static_data.OUT = original_out

    def test_h30533_frontend_uses_current_label_without_legacy_basket_wording(self):
        root = Path(__file__).parents[1]
        html = root.joinpath("static/index.html").read_text()
        streamlit = root.joinpath("app/main.py").read_text()
        self.assertIn("中证海外中国互联网50（H30533）", html)
        self.assertIn("中证海外中国互联网50（H30533）", streamlit)
        for stale in ("23 家美股", "自定义 EPS", "固定参考PE", "US_REF_PE", "近8年"):
            self.assertNotIn(stale, html)
            self.assertNotIn(stale, streamlit)
        self.assertIn("if (!res.ok) throw new Error", html)
        self.assertIn("if (!manifest || !Array.isArray(manifest.artifacts))", html)
        self.assertIn("meta.financial_as_of", html)

    def test_h30533_snapshot_round_trips_all_valuation_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "h30533.db")
            constituents = pd.DataFrame([{
                "security_code": "BABA", "name": "Alibaba", "exchange": "US",
                "weight": 0.8, "issuer_id": "ALIBABA", "security_type": "adr",
                "ads_ratio": 8.0, "source_type": "csi_official",
                "as_of": "2026-10-13", "raw_metadata": {"rank": 1},
                "is_duplicate_issuer": False,
            }])
            daily = pd.DataFrame([{
                "trade_date": "2026-10-14", "index_code": "H30533",
                "pe_ratio": 12.5, "earnings_yield": 0.08, "total_market_cap": 800.0,
                "total_profit_ttm": 64.0, "stock_count": 1,
                "weight_coverage": 0.8, "negative_profit_count": 0,
                "missing_count": 0, "source_type": "csi_official",
                "source_date": "2026-10-13", "constituent_basis": "official",
                "validity_reason": None,
            }])
            securities = pd.DataFrame([{
                "security_code": "BABA", "name": "Alibaba", "issuer_id": "ALIBABA",
                "weight": 0.8, "price": 80.0, "market_cap": 800.0,
                "verified_shares": 10.0, "profit_ttm": 64.0,
                "earnings_yield": 0.08, "pe": 12.5,
                "financial_as_of": "2026-09-30", "source_date": "2026-10-14",
                "source_type": "akshare", "validity_reason": None,
            }])

            cache.save_h30533_snapshot("H30533", constituents, daily, securities)

            loaded = cache.load_h30533_snapshot("H30533")
            self.assertEqual(loaded["constituents"].loc[0, "security_code"], "BABA")
            self.assertEqual(loaded["constituents"].loc[0, "raw_metadata"], {"rank": 1})
            self.assertFalse(loaded["constituents"].loc[0, "is_duplicate_issuer"])
            self.assertAlmostEqual(loaded["daily"].loc[0, "earnings_yield"], 0.08)
            self.assertEqual(loaded["daily"].loc[0, "source_date"], "2026-10-13")
            self.assertAlmostEqual(loaded["securities"].loc[0, "verified_shares"], 10.0)
            self.assertEqual(loaded["securities"].loc[0, "financial_as_of"], "2026-09-30")

    def test_h30533_snapshot_migrates_legacy_schema_and_ignores_extra_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            with sqlite3.connect(path) as conn:
                conn.execute("""CREATE TABLE h30533_daily_valuation (
                    trade_date TEXT NOT NULL, index_code TEXT NOT NULL,
                    pe_ratio REAL, legacy_extra TEXT,
                    PRIMARY KEY (trade_date, index_code)
                )""")
            cache = CacheDB(path)
            cache.save_h30533_daily("H30533", pd.DataFrame([{
                "trade_date": "2026-10-14", "pe_ratio": 12.5,
                "earnings_yield": 0.08, "source_date": "2026-10-13",
            }]))

            with sqlite3.connect(path) as conn:
                columns = {row[1] for row in conn.execute(
                    "PRAGMA table_info(h30533_daily_valuation)"
                )}
            self.assertIn("legacy_extra", columns)
            self.assertEqual(cache.load_h30533_daily("H30533").loc[0, "pe_ratio"], 12.5)

    def test_h30533_migrates_every_core_column_and_round_trips_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE h30533_constituent_snapshot (
                        snapshot_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        security_code TEXT NOT NULL, legacy_constituent TEXT,
                        PRIMARY KEY (snapshot_date, index_code, security_code)
                    );
                    CREATE TABLE h30533_daily_valuation (
                        trade_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        legacy_daily TEXT,
                        PRIMARY KEY (trade_date, index_code)
                    );
                    CREATE TABLE h30533_security_valuation (
                        snapshot_date TEXT NOT NULL, index_code TEXT NOT NULL,
                        security_code TEXT NOT NULL, legacy_security TEXT,
                        PRIMARY KEY (snapshot_date, index_code, security_code)
                    );
                """)

            cache = CacheDB(path)
            constituents = pd.DataFrame([{
                "security_code": "BABA", "name": "Alibaba", "exchange": "US",
                "weight": 0.8, "issuer_id": "ALIBABA", "security_type": "adr",
                "ads_ratio": 8.0, "source_type": "csi_official",
                "as_of": "2026-10-13", "constituent_as_of": "2026-10-13",
                "raw_metadata": {"rank": 1}, "validity_reason": "valid",
                "is_duplicate_issuer": True,
            }])
            daily = pd.DataFrame([{
                "trade_date": "2026-10-14", "index_code": "H30533",
                "pe_ratio": 12.5, "index_pe": 12.5, "earnings_yield": 0.08,
                "total_market_cap": 800.0, "total_profit_ttm": 64.0,
                "stock_count": 1, "valid_count": 1, "weight_coverage": 0.8,
                "negative_profit_count": 0, "missing_count": 0,
                "source_type": "csi_official", "source_date": "2026-10-13",
                "constituent_as_of": "2026-10-13", "constituent_basis": "official",
                "validity_reason": "valid",
            }])
            securities = pd.DataFrame([{
                "security_code": "BABA", "name": "Alibaba", "issuer_id": "ALIBABA",
                "weight": 0.8, "price": 80.0, "currency": "USD", "market_cap": 800.0,
                "verified_shares": 10.0, "profit_ttm": 64.0, "earnings_yield": 0.08,
                "index_pe": 12.5, "pe": 12.5, "financial_as_of": "2026-09-30",
                "source_date": "2026-10-14", "source_type": "akshare",
                "source": "USDataSource", "data_source": "akshare",
                "validity_reason": "valid",
            }])

            cache.save_h30533_snapshot("H30533", constituents, daily, securities)
            loaded = cache.load_h30533_snapshot("H30533")

            self.assertEqual(loaded["constituents"].loc[0, "constituent_as_of"], "2026-10-13")
            self.assertEqual(loaded["constituents"].loc[0, "raw_metadata"], {"rank": 1})
            self.assertTrue(loaded["constituents"].loc[0, "is_duplicate_issuer"])
            self.assertEqual(loaded["daily"].loc[0, "valid_count"], 1)
            self.assertAlmostEqual(loaded["daily"].loc[0, "index_pe"], 12.5)
            self.assertEqual(loaded["securities"].loc[0, "currency"], "USD")
            self.assertEqual(loaded["securities"].loc[0, "source"], "USDataSource")
            self.assertEqual(loaded["securities"].loc[0, "data_source"], "akshare")

            expected_columns = {
                "h30533_constituent_snapshot": {
                    "snapshot_date", "index_code", "security_code", "name", "exchange",
                    "weight", "issuer_id", "security_type", "ads_ratio", "source_type",
                    "as_of", "constituent_as_of", "raw_metadata", "validity_reason",
                    "is_duplicate_issuer", "legacy_constituent",
                },
                "h30533_daily_valuation": {
                    "trade_date", "index_code", "pe_ratio", "index_pe", "earnings_yield",
                    "total_market_cap", "total_profit_ttm", "stock_count", "valid_count",
                    "weight_coverage", "negative_profit_count", "missing_count", "source_type",
                    "source_date", "constituent_as_of", "constituent_basis", "validity_reason",
                    "updated_at", "legacy_daily",
                },
                "h30533_security_valuation": {
                    "snapshot_date", "index_code", "security_code", "name", "issuer_id",
                    "weight", "price", "currency", "market_cap", "verified_shares", "profit_ttm",
                    "earnings_yield", "index_pe", "pe", "financial_as_of", "source_date",
                    "source_type", "source", "data_source", "validity_reason", "legacy_security",
                },
            }
            with sqlite3.connect(path) as conn:
                for table, columns in expected_columns.items():
                    actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                    self.assertTrue(columns <= actual, (table, columns - actual))

    def test_failed_h30533_replacement_keeps_last_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "h30533.db")
            valid = pd.DataFrame([{
                "trade_date": "2026-10-14", "index_code": "H30533",
                "pe_ratio": 12.5,
            }])
            cache.save_h30533_daily("H30533", valid)

            with self.assertRaises((KeyError, ValueError)):
                cache.save_h30533_daily("H30533", pd.DataFrame([{
                    "trade_date": "2026-10-15", "not_a_column": 1,
                }]))

            self.assertEqual(
                cache.load_h30533_daily("H30533").loc[0, "trade_date"], "2026-10-14"
            )

    def test_aggregate_h30533_uses_weighted_earnings_yield(self):
        rows = pd.DataFrame([
            {"weight": 0.25, "market_cap": 100.0, "profit_ttm": 10.0},
            {"weight": 0.50, "market_cap": 200.0, "profit_ttm": 20.0},
        ])

        result = aggregate_h30533(rows, min_weight_coverage=0.7)

        expected_yield = 0.25 * (10 / 100) + 0.50 * (20 / 200)
        self.assertAlmostEqual(result["earnings_yield"], expected_yield)
        self.assertAlmostEqual(result["pe"], 1 / expected_yield)
        self.assertEqual(result["weight_coverage"], 0.75)
        self.assertTrue(result["valid"])

    def test_aggregate_h30533_counts_negative_profit_without_using_it(self):
        rows = pd.DataFrame([
            {"weight": 0.60, "market_cap": 100.0, "profit_ttm": 10.0},
            {"weight": 0.20, "market_cap": 100.0, "profit_ttm": -5.0},
        ])

        result = aggregate_h30533(rows)

        self.assertAlmostEqual(result["earnings_yield"], 0.06)
        self.assertEqual(result["negative_profit_count"], 1)
        self.assertEqual(result["weight_coverage"], 0.6)
        self.assertIsNone(result["pe"])
        self.assertFalse(result["valid"])
        self.assertIn("coverage", result["reason"])

    def test_aggregate_h30533_does_not_renormalize_missing_weights(self):
        rows = pd.DataFrame([
            {"weight": 0.79, "market_cap": 100.0, "profit_ttm": 10.0},
            {"weight": 0.21, "market_cap": None, "profit_ttm": 10.0},
        ])

        result = aggregate_h30533(rows)

        self.assertEqual(result["weight_coverage"], 0.79)
        self.assertIsNone(result["pe"])
        self.assertEqual(result["missing_count"], 1)
        self.assertIn("coverage", result["reason"])

    def test_aggregate_h30533_can_use_verified_shares_and_price(self):
        rows = pd.DataFrame([{
            "weight": 0.8,
            "price": 12.0,
            "verified_shares": 10.0,
            "profit_ttm": 24.0,
        }])

        result = aggregate_h30533(rows)

        self.assertAlmostEqual(result["earnings_yield"], 0.16)
        self.assertAlmostEqual(result["pe"], 6.25)

    def test_aggregate_h30533_converts_profit_into_market_cap_currency(self):
        rows = pd.DataFrame([{
            "weight": 1.0, "market_cap": 720.0, "market_cap_currency": "USD",
            "profit_ttm": 720.0, "currency": "CNY",
        }])

        unavailable = aggregate_h30533(rows)
        converted = aggregate_h30533(rows, fx_rates={"CNY/USD": 7.2})

        self.assertFalse(unavailable["valid"])
        self.assertIn("currency", unavailable["reason"])
        self.assertAlmostEqual(converted["earnings_yield"], 1 / 7.2)
        self.assertAlmostEqual(converted["pe"], 7.2)

    def test_official_validation_error_falls_back_and_retains_structure(self):
        calls = []

        def fetcher(url, params):
            calls.append(params)
            if params.get("indexCode"):
                return {"data": {"indexCode": "000300", "asOfDate": "2026-10-13", "constituents": []}}
            return {
                "fundCode": CHINA_INTL_FUND_CODE,
                "as_of": "2026-10-10",
                "holdings": [{"code": "00700", "name": "Tencent", "market": "HK", "weight_pct": 7.1}],
            }

        source = H30533Source(fetcher=fetcher, retries=0)
        result = source.get_snapshot()

        self.assertEqual(result.iloc[0]["source_type"], "etf_disclosure")
        self.assertEqual(calls, [
            {"indexCode": CHINA_INTL_INDEX_CODE},
            {
                "type": "jjcc",
                "code": CHINA_INTL_FUND_CODE,
                "topline": "10",
                "year": "",
                "month": "",
                "rt": "1",
            },
        ])
        self.assertEqual(source.source_errors[0]["code"], "wrong_index")
        self.assertEqual(source.source_errors[0]["details"]["expected"], CHINA_INTL_INDEX_CODE)

    def test_run_historical_persists_backfill_metadata(self):
        etl = USETL(cache=MagicMock())
        etl.h30533_source = MagicMock()
        etl.source = MagicMock()
        etl.h30533_source.get_snapshot.return_value = pd.DataFrame([{
            "security_code": "BABA", "name": "Alibaba", "weight": 1.0,
            "source_type": "etf_disclosure", "as_of": "2026-10-13",
        }])
        etl.source.get_current_pe.return_value = {
            "code": "BABA", "price": 80.0, "market_cap": 800.0,
            "profit_ttm": 80.0, "currency": "USD", "financial_as_of": "2026-09-30",
        }
        etl.source.get_daily_quotes.return_value = pd.DataFrame([{"date": "2026-10-14", "close": 80.0}])
        etl.source._get_fx_rate.return_value = 7.2

        with patch("etl.pipeline_us.API_BATCH_DELAY", 0), patch.object(etl, "_recompute_us_stats"):
            result = etl.run_historical()

        self.assertEqual(result.iloc[0]["constituent_basis"], "current_constituents_backfill")
        etl.cache.save_h30533_snapshot.assert_called_once()
        saved_daily = etl.cache.save_h30533_snapshot.call_args.args[2]
        self.assertEqual(saved_daily.iloc[0]["constituent_basis"], "current_constituents_backfill")

    def test_pipeline_uses_h30533_source_and_verified_records(self):
        source = USETL.__module__
        with open(__import__(source, fromlist=["__file"]).__file__, encoding="utf-8") as handle:
            implementation = handle.read()

        self.assertIn("H30533Source", implementation)
        self.assertIn("get_current_pe", implementation)
        self.assertNotIn("US_REF_PE", implementation)
        self.assertNotIn("price ** 2", implementation)
        self.assertNotIn("price**2", implementation)

    def test_pipeline_consumes_deduplicated_h30533_snapshot(self):
        etl = USETL(cache=MagicMock())
        etl.h30533_source = MagicMock()
        etl.source = MagicMock()
        etl.h30533_source.get_snapshot.return_value = pd.DataFrame([{
            "security_code": "BABA",
            "name": "Alibaba",
            "weight": 0.8,
            "source_type": "csi_official",
            "as_of": "2026-10-13",
        }])
        etl.source.get_current_pe.return_value = {
            "price": 80.0,
            "market_cap": 800.0,
            "profit_ttm": 80.0,
        }

        with patch.object(etl, "_recompute_us_stats"):
            result = etl.run()

        self.assertEqual(result.iloc[0]["stock_count"], 1)
        etl.h30533_source.get_snapshot.assert_called_once_with()
        etl.source.get_current_pe.assert_called_once_with("BABA")

    def test_current_pe_maps_verified_market_inputs_from_quote_and_financial_records(self):
        source = USDataSource()
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{
            "close": 80.0, "market_cap": 800.0, "shares_outstanding": 10.0,
        }])), patch.object(source, "get_financials", return_value=pd.DataFrame([{
            "EPS_TTM": 2.0, "CURRENCY": "USD", "REPORT_DATE": "2026-09-30",
            "NET_PROFIT_TTM": 80.0,
        }])):
            record = source.get_current_pe("BABA")

        self.assertEqual(record["market_cap"], 800.0)
        self.assertEqual(record["verified_shares"], 10.0)
        self.assertEqual(record["profit_ttm"], 80.0)

    def test_pipeline_aggregates_snapshot_records_with_negative_profit_count(self):
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
            {"price": 80.0, "market_cap": 800.0, "profit_ttm": 80.0},
            {"price": 50.0, "market_cap": 500.0, "profit_ttm": -5.0},
        ]

        with patch.object(etl, "_recompute_us_stats"):
            result = etl.run()

        self.assertAlmostEqual(result.iloc[0]["pe_ratio"], 12.5)
        self.assertAlmostEqual(result.iloc[0]["earnings_yield"], 0.08)
        self.assertEqual(result.iloc[0]["negative_profit_count"], 1)
        self.assertEqual(result.iloc[0]["index_code"], CHINA_INTL_INDEX_CODE)
        self.assertEqual(result.iloc[0]["source_type"], "csi_official")

    def test_pipeline_persists_invalid_h30533_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDB(Path(directory) / "h30533.db")
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
                {"price": None, "market_cap": None, "profit_ttm": None,
                 "validity_reason": "missing price; missing profit_ttm"},
                {"price": 50.0, "market_cap": 500.0, "profit_ttm": -5.0,
                 "validity_reason": "non_positive profit_ttm"},
            ]

            with patch.object(etl, "_recompute_us_stats"), \
                 patch("etl.pipeline_us.API_BATCH_DELAY", 0):
                result = etl.run()

            self.assertEqual(len(result), 1)
            self.assertIsNone(result.iloc[0]["pe_ratio"])
            self.assertIsNone(result.iloc[0]["index_pe"])
            self.assertEqual(result.iloc[0]["stock_count"], 0)
            self.assertEqual(result.iloc[0]["weight_coverage"], 0)
            self.assertEqual(result.iloc[0]["missing_count"], 1)
            self.assertEqual(result.iloc[0]["negative_profit_count"], 1)
            self.assertEqual(result.iloc[0]["invalid_count"], 0)
            self.assertIn("coverage", result.iloc[0]["validity_reason"])

            snapshot = cache.load_h30533_snapshot(CHINA_INTL_INDEX_CODE)
            self.assertEqual(snapshot["constituents"]["security_code"].tolist(), ["BABA", "PDD"])
            self.assertEqual(snapshot["securities"]["security_code"].tolist(), ["BABA", "PDD"])
            self.assertEqual(
                snapshot["securities"]["validity_reason"].tolist(),
                ["missing price; missing profit_ttm", "non_positive profit_ttm"],
            )
            self.assertEqual(snapshot["daily"].iloc[0]["valid_count"], 0)
            self.assertEqual(snapshot["daily"].iloc[0]["weight_coverage"], 0)
            self.assertEqual(snapshot["daily"].iloc[0]["invalid_count"], 0)
            self.assertIsNone(snapshot["daily"].iloc[0]["index_pe"])
            self.assertEqual(snapshot["daily"].iloc[0]["source_type"], "csi_official")
            self.assertEqual(snapshot["daily"].iloc[0]["source_date"], "2026-10-13")
            self.assertEqual(snapshot["daily"].iloc[0]["constituent_as_of"], "2026-10-13")

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

    def test_parse_live_etf_disclosure_script_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "fund_513050_holdings.js"
        source = H30533Source()

        result = source.parse_etf_disclosure(fixture.read_text())

        self.assertEqual(result["as_of"].unique().tolist(), ["2026-09-30"])
        self.assertEqual(result["security_code"].tolist(), ["00700.HK", "BABA"])
        self.assertEqual(result["name"].tolist(), ["腾讯控股", "阿里巴巴"])
        self.assertEqual(result["weight"].tolist(), [0.0825, 0.051])
        self.assertEqual(result["source_type"].unique().tolist(), ["etf_disclosure"])

    def test_get_snapshot_uses_eastmoney_etf_request_contract(self):
        calls = []
        fixture = Path(__file__).parent / "fixtures" / "fund_513050_holdings.js"

        self.assertEqual(
            H30533Source.ETF_URL,
            "https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
        )

        def fetcher(url, params):
            calls.append((url, params))
            if params.get("indexCode"):
                raise RuntimeError("CSI unavailable")
            return fixture.read_text()

        source = H30533Source(fetcher=fetcher, retries=0)

        result = source.get_snapshot()

        self.assertEqual(result.iloc[0]["source_type"], "etf_disclosure")
        self.assertEqual(calls, [
            (source.official_url, {"indexCode": CHINA_INTL_INDEX_CODE}),
            (source.etf_url, {
                "type": "jjcc",
                "code": CHINA_INTL_FUND_CODE,
                "topline": "10",
                "year": "",
                "month": "",
                "rt": "1",
            }),
        ])

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
        self.assertEqual(calls, [
            {"indexCode": CHINA_INTL_INDEX_CODE},
            {
                "type": "jjcc",
                "code": CHINA_INTL_FUND_CODE,
                "topline": "10",
                "year": "",
                "month": "",
                "rt": "1",
            },
        ])

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

    def test_current_pe_marks_cny_profit_unavailable_when_fx_fetch_fails(self):
        source = USDataSource()
        with patch.object(source, "get_daily_quotes", return_value=pd.DataFrame([{"close": 80.0, "market_cap": 800.0}])), \
             patch.object(source, "get_financials", return_value=pd.DataFrame([{
                 "EPS_TTM": 2.0, "PROFIT_TTM": 80.0, "CURRENCY": "CNY",
                 "REPORT_DATE": "2026-09-30",
             }])), patch.object(source, "_get_fx_rate", return_value=None):
            record = source.get_current_pe("BABA")

        self.assertIn("CNY/USD conversion unavailable", record["validity_reason"])
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
