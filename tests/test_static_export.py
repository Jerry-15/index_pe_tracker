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
        self.assertEqual(entry["data_file"], "CHINA_INTL.json")
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
        self.assertEqual(index_list[0]["data_file"], "CHINA_INTL.json")
        self.assertEqual(json.loads(
            (Path(self._tmpdir.name) / "CHINA_INTL.json").read_text(encoding="utf-8")
        )[0][0:2], ["2026-10-14", 12.5])

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
        self.assertEqual(json.loads((Path(self._tmpdir.name) / "CHINA_INTL.json").read_text()), [])

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

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
