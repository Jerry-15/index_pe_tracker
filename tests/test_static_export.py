import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts import export_static_data


class StaticExportTests(unittest.TestCase):
    def test_committed_h30533_artifacts_reference_each_other(self):
        root = Path(__file__).parents[1] / "static" / "data"
        index_list = json.loads((root / "index_list.json").read_text(encoding="utf-8"))
        entry = next(item for item in index_list if item["code"] == "H30533")
        self.assertEqual(entry["data_file"], "H30533.json")
        self.assertEqual(entry["type"], "h30533")
        self.assertTrue((root / entry["data_file"]).exists())
        rows = json.loads((root / entry["data_file"]).read_text(encoding="utf-8"))
        self.assertTrue(rows)
        self.assertGreaterEqual(len(rows[-1]), 18)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["generated_at"])

    def test_static_loader_handles_http_json_errors_and_preserves_selection(self):
        source = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("if (!res.ok)", source)
        self.assertIn("JSON 格式无效", source)
        self.assertIn("const previous = currentCode || sel.value", source)
        self.assertIn("无法加载 ${url}", source)
        self.assertIn("renderValidity(meta, rows)", source)
        self.assertIn("if (!rows.length) return", source)
        self.assertIn("financial_as_of", source)

    def test_h30533_updates_legacy_entry_in_place_and_keeps_metadata(self):
        class FakeCache:
            def load_h30533_snapshot(self, code):
                return {
                    "daily": pd.DataFrame([{
                        "trade_date": "2026-10-14", "pe_ratio": 12.5,
                        "stock_count": 2, "valid_count": 1, "weight_coverage": 0.6,
                        "source_type": "csi_official", "source_date": "2026-10-14",
                        "constituent_as_of": "2026-10-13", "constituent_basis": "H30533",
                        "validity_reason": "partial coverage",
                    }]),
                    "constituents": pd.DataFrame([{"security_code": "BABA"}]),
                    "securities": pd.DataFrame([{
                        "security_code": "BABA", "name": "Alibaba", "pe": 12.5,
                        "validity_reason": None,
                    }]),
                }

        legacy = {
            "code": "CHINA_INTL", "name": "旧名称", "type": "us_stock",
            "custom_metadata": "preserve", "data_file": "CHINA_INTL.json",
        }
        with self.subTest("export"), patch.object(export_static_data, "OUT", Path(self._tmpdir.name)):
            (Path(self._tmpdir.name) / "index_list.json").write_text(
                json.dumps([legacy]), encoding="utf-8"
            )
            export_static_data._export_china_intl(FakeCache(), object())

        index_list = json.loads(
            (Path(self._tmpdir.name) / "index_list.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(index_list), 1)
        self.assertEqual(index_list[0]["code"], "H30533")
        self.assertEqual(index_list[0]["custom_metadata"], "preserve")
        self.assertEqual(index_list[0]["data_file"], "H30533.json")
        self.assertEqual(json.loads(
            (Path(self._tmpdir.name) / "H30533.json").read_text(encoding="utf-8")
        )[0][0:2], ["2026-10-14", 12.5])
        self.assertFalse((Path(self._tmpdir.name) / "CHINA_INTL.json").exists())

    def test_h30533_empty_snapshot_does_not_use_legacy_daily_data(self):
        class EmptyCache:
            def load_h30533_snapshot(self, code):
                return {
                    "daily": pd.DataFrame(),
                    "constituents": pd.DataFrame(),
                    "securities": pd.DataFrame(),
                }

            def load_us_pe_daily(self):
                return pd.DataFrame([{"trade_date": "2026-10-14", "pe_ratio": 9.9}])

        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)):
            (Path(self._tmpdir.name) / "index_list.json").write_text("[]", encoding="utf-8")
            export_static_data._export_china_intl(EmptyCache(), object())

        entry = json.loads((Path(self._tmpdir.name) / "index_list.json").read_text())[0]
        self.assertEqual(entry["code"], "H30533")
        self.assertTrue(entry["unavailable"])
        self.assertIn("H30533 snapshot is unavailable", entry["validity_reason"])
        self.assertEqual(entry["financial_as_of"], None)
        self.assertEqual(json.loads((Path(self._tmpdir.name) / "H30533.json").read_text()), [])

    def test_export_all_preserves_catalog_and_manifests_generated_artifacts(self):
        class FakeCache:
            def load_pe_daily(self, code):
                return pd.DataFrame([{"trade_date": "2026-10-14", "pe_ratio": 10.0}])

            def load_pe_stats(self, code):
                return pd.DataFrame()

            def load_h30533_snapshot(self, code):
                return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

        class FakeSource:
            def get_industry_reference(self):
                return pd.DataFrame([{"code": "801010", "name": "农林牧渔", "pe_ttm": 20.0}])

            def get_index_pe_history(self, name):
                return pd.DataFrame()

        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)), \
             patch.object(export_static_data, "CacheDB", return_value=FakeCache()), \
             patch.object(export_static_data, "get_source", return_value=FakeSource()), \
             patch.object(export_static_data, "INDEX_CONFIG", {"沪深300": {"code": "000300"}}):
            out = Path(self._tmpdir.name)
            (out / "index_list.json").write_text(
                json.dumps([{"code": "801999", "name": "保留目录项", "type": "industry"}]),
                encoding="utf-8",
            )
            export_static_data.export_all()

        catalog = json.loads((out / "index_list.json").read_text(encoding="utf-8"))
        self.assertEqual({item["code"] for item in catalog}, {"801999", "000300", "801010", "H30533"})
        self.assertEqual(sum(item["code"] == "H30533" for item in catalog), 1)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["artifacts"]), {"000300.json", "801010.json", "H30533.json", "index_list.json"})

    def test_h30533_exports_financial_as_of_metadata(self):
        class FinancialCache:
            def load_h30533_snapshot(self, code):
                return {
                    "daily": pd.DataFrame([{
                        "trade_date": "2026-10-14", "pe_ratio": 12.5,
                        "source_date": "2026-10-14", "constituent_as_of": "2026-10-13",
                        "constituent_basis": "H30533", "financial_as_of": "2026-09-30",
                    }]),
                    "constituents": pd.DataFrame(),
                    "securities": pd.DataFrame([{
                        "security_code": "BABA", "financial_as_of": "2026-09-30",
                    }]),
                }

        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)):
            (Path(self._tmpdir.name) / "index_list.json").write_text("[]", encoding="utf-8")
            export_static_data._export_china_intl(FinancialCache(), object())

        entry = json.loads((Path(self._tmpdir.name) / "index_list.json").read_text())[0]
        self.assertEqual(entry["financial_as_of"], "2026-09-30")
        self.assertEqual(entry["stock_diagnostics"][0]["financial_as_of"], "2026-09-30")

    def test_export_rows_include_quality_metadata_without_coercing_missing_pe(self):
        rows = export_static_data._to_rows(pd.DataFrame([{
            "trade_date": "2026-10-14", "pe_ratio": None,
            "quality_level": "C", "source_type": "csi_official",
            "generated_at": "2026-10-15T00:00:00+00:00", "weight_coverage": 0.94,
            "financial_as_of": "2026-09-30", "validity_reason": "insufficient coverage",
        }]), h30533=True, generated_at="2026-10-15T00:00:00+00:00")

        self.assertIsNone(rows[0][1])
        self.assertEqual(rows[0][-7:], ["C", "csi_official", "2026-10-15T00:00:00+00:00",
                                         0.94, "2026-09-30", "insufficient coverage", None])

    def test_regular_rows_and_catalog_use_the_quality_contract(self):
        rows = export_static_data._to_rows(pd.DataFrame([{
            "trade_date": "2026-10-14", "pe_ratio": 12.5,
            "quality_level": "B", "data_source": "sina",
            "generated_at": "2026-10-15T00:00:00+00:00", "coverage_ratio": 0.98,
            "financial_as_of": "2026-09-30", "validity_reason": None,
        }]), generated_at="2026-10-15T00:00:00+00:00")

        self.assertEqual(rows[0][12:], ["B", "sina", "2026-10-15T00:00:00+00:00",
                                       0.98, "2026-09-30", None])

    def test_regular_catalog_marks_missing_pipeline_quality_as_c(self):
        entry = {}
        export_static_data._add_quality_catalog_metadata(
            entry, pd.Series({"validity_reason": None}), "2026-10-15T00:00:00+00:00"
        )
        self.assertEqual(entry["quality_level"], "C")
        self.assertEqual(entry["validity"], "quality metadata unavailable from pipeline/cache")

    def test_manifest_contains_revision_counts_and_date_ranges(self):
        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)), \
             patch.object(export_static_data, "_source_revision", return_value="a04aa73"):
            out = Path(self._tmpdir.name)
            (out / "000300.json").write_text(json.dumps([
                ["2026-01-01", 10], ["2026-10-14", 12]
            ]), encoding="utf-8")
            (out / "index_list.json").write_text("[]", encoding="utf-8")
            export_static_data._save_manifest("2026-10-15T00:00:00+00:00")

        manifest = json.loads((Path(self._tmpdir.name) / "manifest.json").read_text())
        self.assertEqual(manifest["schema_version"], "quality-export-v1")
        self.assertEqual(manifest["source_revision"], "a04aa73")
        self.assertEqual(manifest["item_count"], 2)
        self.assertEqual(manifest["date_ranges"]["000300.json"],
                         {"min": "2026-01-01", "max": "2026-10-14"})

    def test_source_revision_returns_full_head(self):
        with patch.object(export_static_data.subprocess, "check_output", return_value="abcdef123456\n") as run:
            self.assertEqual(export_static_data._source_revision(), "abcdef123456")
            self.assertEqual(run.call_args.args[0], ["git", "rev-parse", "HEAD"])

    def test_empty_h30533_export_is_empty_and_has_no_legacy_provenance(self):
        class EmptyCache:
            def load_h30533_snapshot(self, code):
                return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)):
            out = Path(self._tmpdir.name)
            (out / "index_list.json").write_text("[]", encoding="utf-8")
            (out / "H30533.json").write_text(json.dumps([["legacy", 9.9]]), encoding="utf-8")
            export_static_data._export_china_intl(EmptyCache(), object(), generated_at="now")

        self.assertEqual(json.loads((Path(self._tmpdir.name) / "H30533.json").read_text()), [])
        entry = json.loads((Path(self._tmpdir.name) / "index_list.json").read_text())[0]
        self.assertEqual(entry["quality_level"], "C")
        self.assertEqual(entry["validity_reason"],
                         "H30533 snapshot is unavailable; no H30533 daily valuation rows")
        self.assertIsNone(entry["coverage"])
        self.assertIsNone(entry["financial_as_of"])
        self.assertFalse(entry["current_constituents_backfill"])

    def test_h30533_entry_exports_quality_provenance_diagnostics_and_backfill(self):
        class QualityCache:
            def load_h30533_snapshot(self, code):
                return {
                    "daily": pd.DataFrame([{
                        "trade_date": "2026-10-14", "pe_ratio": None,
                        "quality_level": "C", "source_type": "etf_disclosure",
                        "source_date": "2026-10-10", "constituent_as_of": "2026-10-10",
                        "constituent_basis": "H30533", "weight_coverage": 0.94,
                        "valid_count": 1, "negative_profit_count": 0, "missing_count": 1,
                        "financial_stale_count": 1, "derived_market_cap_count": 0,
                        "quote_disagreement_count": 0, "excluded_count": 2,
                        "validity_reason": "financial_stale", "financial_as_of": "2026-09-30",
                        "current_constituents_backfill": True,
                        "current_constituents_caveat": "historical values use current constituents",
                        "source_url": "https://source.test", "fetched_at": "2026-10-10T00:00:00Z",
                        "response_hash": "abc",
                    }]),
                    "constituents": pd.DataFrame([{"security_code": "BABA"}]),
                    "securities": pd.DataFrame([{"security_code": "BABA", "financial_as_of": "2026-09-30"}]),
                }

        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)):
            (Path(self._tmpdir.name) / "index_list.json").write_text("[]", encoding="utf-8")
            export_static_data._export_china_intl(QualityCache(), object(),
                                                  generated_at="2026-10-15T00:00:00+00:00")

        entry = json.loads((Path(self._tmpdir.name) / "index_list.json").read_text())[0]
        self.assertEqual(entry["quality_level"], "C")
        self.assertEqual(entry["source_url"], "https://source.test")
        self.assertEqual(entry["diagnostics"]["financial_stale_count"], 1)
        self.assertTrue(entry["current_constituents_backfill"])
        self.assertIsNone(json.loads((Path(self._tmpdir.name) / "H30533.json").read_text())[0][1])

    def test_h30533_populated_snapshot_uses_actual_quality_reason(self):
        class QualityCache:
            def load_h30533_snapshot(self, code):
                return {"daily": pd.DataFrame([{
                    "trade_date": "2026-10-14", "pe_ratio": None,
                    "quality_level": "C", "validity_reason": "currency_unavailable",
                    "source_type": "csi_official", "source_url": "https://source.test",
                }]), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)):
            (Path(self._tmpdir.name) / "index_list.json").write_text("[]", encoding="utf-8")
            export_static_data._export_china_intl(QualityCache(), object())

        entry = json.loads((Path(self._tmpdir.name) / "index_list.json").read_text())[0]
        self.assertEqual(entry["validity_reason"], "currency_unavailable")
        self.assertFalse(entry["unavailable"])

    def test_h30533_populated_snapshot_is_not_catalogued_as_unavailable(self):
        class PopulatedCache:
            def load_h30533_snapshot(self, code):
                return {"daily": pd.DataFrame([{
                    "trade_date": "2026-10-14", "pe_ratio": 12.5,
                    "quality_level": "B", "validity_reason": None,
                    "source_type": "csi_official", "data_source": "csi_official",
                    "source_url": "https://source.test",
                }]), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)):
            (Path(self._tmpdir.name) / "index_list.json").write_text("[]", encoding="utf-8")
            export_static_data._export_china_intl(PopulatedCache(), object())

        entry = json.loads((Path(self._tmpdir.name) / "index_list.json").read_text())[0]
        self.assertFalse(entry["unavailable"])
        self.assertEqual(entry["data_source"], "csi_official")

    def test_unavailable_h30533_export_keeps_snapshot_metadata(self):
        class MetadataCache:
            def load_h30533_snapshot(self, code):
                return {"daily": pd.DataFrame(), "constituents": pd.DataFrame([{
                    "source_type": "etf_disclosure", "source_url": "https://source.test",
                    "fetched_at": "2026-10-15T00:00:00Z", "response_hash": "hash",
                    "as_of": "2026-10-14",
                }]), "securities": pd.DataFrame()}

        with patch.object(export_static_data, "OUT", Path(self._tmpdir.name)):
            (Path(self._tmpdir.name) / "index_list.json").write_text("[]", encoding="utf-8")
            export_static_data._export_china_intl(MetadataCache(), object())

        entry = json.loads((Path(self._tmpdir.name) / "index_list.json").read_text())[0]
        self.assertEqual(entry["source_type"], "etf_disclosure")
        self.assertEqual(entry["source_url"], "https://source.test")
        self.assertEqual(entry["response_hash"], "hash")

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
