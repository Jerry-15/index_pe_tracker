import json
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts import export_static_data
from scripts.export_static_data import _to_rows


class StaticDeliveryTests(unittest.TestCase):
    def test_export_catalog_preserves_existing_entries_and_manifest(self):
        class EmptyCache:
            def load_pe_daily(self, code):
                return pd.DataFrame()

            def load_pe_stats(self, code):
                return pd.DataFrame()

            def load_h30533_snapshot(self, code):
                return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for code in ("000300", "801010"):
                (output / f"{code}.json").write_text("[]")
            (output / "index_list.json").write_text(json.dumps([
                {"code": "000300", "name": "沪深300", "type": "index", "data_file": "000300.json"},
                {"code": "801010", "name": "农林牧渔", "type": "industry", "data_file": "801010.json"},
                {"code": "CHINA_INTL", "name": "旧名称", "data_file": "CHINA_INTL.json"},
            ]))
            with patch.object(export_static_data, "OUT", output), patch.object(export_static_data, "CacheDB", EmptyCache), patch.object(export_static_data, "get_source", lambda: None):
                export_static_data.export_all()

            index_list = json.loads((output / "index_list.json").read_text())
            self.assertEqual([item["code"] for item in index_list], ["000300", "801010", "H30533"])
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(set(manifest["artifacts"]), {
                "index_list.json", "000300.json", "801010.json", "CHINA_INTL.json",
            })

    def test_export_regenerates_committed_catalog_artifacts(self):
        class Cache:
            def load_pe_daily(self, code):
                return pd.DataFrame([{
                    "trade_date": "2026-10-14",
                    "pe_ratio": 12.5,
                    "valid_stock_count": 3,
                    "profitable_stock_count": 3,
                    "coverage_ratio": 1.0,
                    "validity_reason": None,
                }])

            def load_pe_stats(self, code):
                return pd.DataFrame()

            def load_h30533_snapshot(self, code):
                return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

        class Source:
            def get_index_pe_history(self, name):
                return pd.DataFrame()

            def get_industry_reference(self):
                return pd.DataFrame([{"code": "801010", "name": "农林牧渔", "pe_ttm": 20.0}])

        root = Path(__file__).parents[1]
        committed_catalog = json.loads((root / "static/data/index_list.json").read_text())
        protected_payloads = {
            path: path.read_bytes() for path in (root / "static/data").glob("*.json")
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "index_list.json").write_text(json.dumps(committed_catalog))
            for item in committed_catalog:
                (output / item["data_file"]).write_text("[]")
            with patch.object(export_static_data, "OUT", output), patch.object(export_static_data, "CacheDB", Cache), patch.object(export_static_data, "get_source", Source):
                export_static_data.export_all()

            catalog = json.loads((output / "index_list.json").read_text())
            by_code = {item["code"]: item for item in catalog}
            self.assertEqual(by_code["000300"]["data_file"], "000300.json")
            self.assertEqual(by_code["801010"]["data_file"], "801010.json")
            self.assertEqual(by_code["H30533"]["data_file"], "CHINA_INTL.json")
            self.assertEqual(json.loads((output / "000300.json").read_text()), [
                ["2026-10-14", 12.5, None, None, None, None, None, None, 3, 3, 1.0, None],
            ])
            self.assertTrue((output / "801010.json").exists())
            self.assertTrue((output / "CHINA_INTL.json").exists())
            self.assertFalse((output / "H30533.json").exists())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(set(manifest["artifacts"]),
                             {"index_list.json", *(item["data_file"] for item in catalog)})
        self.assertEqual(
            protected_payloads,
            {path: path.read_bytes() for path in (root / "static/data").glob("*.json")},
        )

    def test_main_definition_and_guard_prevent_import_nameerror(self):
        source = Path(__file__).parents[1] / "app/main.py"
        tree = ast.parse(source.read_text())
        self.assertEqual(
            [node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"],
            ["main"],
        )
        self.assertIn("if __name__ == \"__main__\":\n    main()", source.read_text())

    def test_streamlit_and_static_chart_semantics_are_identical(self):
        root = Path(__file__).parents[1]
        streamlit = (root / "app/main.py").read_text()
        html = (root / "static/index.html").read_text()

        labels = ("高估线(μ+1σ)", "低估线(μ-1σ)", "高估 80%", "中位 50%", "低估 20%")
        for label in labels:
            self.assertIn(label, streamlit)
            self.assertIn(label, html)
        for stale in ("高估线(均值+1σ)", "低估线(均值-1σ)", "高估区(80%)", "低估区(20%)"):
            self.assertNotIn(stale, streamlit)
            self.assertNotIn(stale, html)

        self.assertIn("rgba(128,128,128,0.14)", streamlit)
        self.assertIn("rgba(128,128,128,0.14)", html)
        self.assertIn("rgba(148,103,189,0.15)", streamlit)
        self.assertIn("rgba(148,103,189,0.15)", html)
        for color in ("#d62728", "#2ca02c", "#bbb"):
            self.assertIn(color, streamlit)
            self.assertIn(color, html)
        self.assertIn("showSymbol:rows.length <= 1", html)
        self.assertIn('mode="lines+markers" if len(pe_df) <= 1 else "lines"', streamlit)


def test_static_rows_are_strict_json_and_keep_validity_metadata():
    df = pd.DataFrame([{
        "trade_date": "2025-01-02",
        "pe_ratio": np.nan,
        "pe_mean": np.inf,
        "pe_high_line": np.nan,
        "pe_low_line": np.nan,
        "pe_percentile": np.nan,
        "total_market_cap": 1000.0,
        "total_profit_ttm": 0.0,
        "valid_stock_count": 1,
        "profitable_stock_count": 0,
        "coverage_ratio": 0.5,
        "validity_reason": "non_positive_profit;pe_out_of_bounds",
    }])

    rows = _to_rows(df)
    encoded = json.dumps(rows, allow_nan=False)

    assert "NaN" not in encoded
    assert rows[0][1] is None
    assert rows[0][8:] == [1, 0, 0.5, "non_positive_profit;pe_out_of_bounds"]


def test_h30533_static_rows_emit_valuation_bands():
    rows = _to_rows(pd.DataFrame([{
        "trade_date": "2026-10-14", "pe_ratio": 12.5, "pe_mean": 10.0,
        "pe_high_line": 12.0, "pe_low_line": 8.0, "pe_percentile": 75.0,
        "stock_count": 1, "valid_count": 1, "weight_coverage": 1.0,
    }]), h30533=True)

    assert rows[0][2:6] == [10.0, 12.0, 8.0, 75.0]


def test_static_export_writes_h30533_unavailable_artifacts(tmp_path, monkeypatch):
    class FakeCache:
        def load_h30533_snapshot(self, code):
            return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

    monkeypatch.setattr(export_static_data, "OUT", tmp_path)
    monkeypatch.setattr(export_static_data, "CacheDB", FakeCache)
    monkeypatch.setattr(export_static_data, "INDEX_CONFIG", {})

    export_static_data.export_all()

    index_list = json.loads((tmp_path / "index_list.json").read_text())
    assert len(index_list) == 1
    assert index_list[0]["code"] == "H30533"
    assert json.loads((tmp_path / "CHINA_INTL.json").read_text()) == []


def test_h30533_export_is_unavailable_without_snapshot_and_writes_manifest(tmp_path, monkeypatch):
    class EmptyCache:
        def load_h30533_snapshot(self, code):
            return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

    monkeypatch.setattr(export_static_data, "OUT", tmp_path)
    monkeypatch.setattr(export_static_data, "CacheDB", EmptyCache)
    monkeypatch.setattr(export_static_data, "get_source", lambda: None)
    monkeypatch.setattr(export_static_data, "INDEX_CONFIG", {})

    export_static_data.export_all()

    index_list = json.loads((tmp_path / "index_list.json").read_text())
    entries = [item for item in index_list if item["type"] == "h30533"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["code"] == "H30533"
    assert entry["data_file"] == "CHINA_INTL.json"
    assert entry["unavailable"] is True
    assert entry["source"] is None
    assert json.loads((tmp_path / entry["data_file"]).read_text()) == []

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["generated_at"]
    assert "index_list.json" in manifest["artifacts"]
    assert "CHINA_INTL.json" in manifest["artifacts"]


def test_export_all_preserves_existing_entries_and_replaces_legacy_h30533(tmp_path, monkeypatch):
    class Cache:
        def load_h30533_snapshot(self, code):
            return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

        def load_pe_daily(self, code):
            raise AssertionError("legacy index data must not be exported")

    monkeypatch.setattr(export_static_data, "OUT", tmp_path)
    monkeypatch.setattr(export_static_data, "CacheDB", Cache)
    monkeypatch.setattr(export_static_data, "get_source", lambda: (_ for _ in ()).throw(AssertionError("legacy source must not be used")))
    (tmp_path / "index_list.json").write_text(json.dumps([
        {"code": "000300", "name": "沪深300"},
        {"code": "801010", "name": "农林牧渔"},
        {"code": "CHINA_INTL", "name": "旧名称"},
    ]))

    export_static_data.export_all()

    index_list = json.loads((tmp_path / "index_list.json").read_text())
    assert [item["code"] for item in index_list] == ["000300", "801010", "H30533"]


def test_export_all_preserves_catalog_files_and_manifest_references(tmp_path, monkeypatch):
    class EmptyCache:
        def load_h30533_snapshot(self, code):
            return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

    monkeypatch.setattr(export_static_data, "OUT", tmp_path)
    monkeypatch.setattr(export_static_data, "CacheDB", EmptyCache)
    for code in ("000300", "801010"):
        (tmp_path / f"{code}.json").write_text("[]")
    (tmp_path / "index_list.json").write_text(json.dumps([
        {"code": "000300", "name": "沪深300", "type": "index", "data_file": "000300.json"},
        {"code": "801010", "name": "农林牧渔", "type": "industry", "data_file": "801010.json"},
        {"code": "CHINA_INTL", "name": "旧名称", "data_file": "CHINA_INTL.json"},
    ]))

    export_static_data.export_all()

    index_list = json.loads((tmp_path / "index_list.json").read_text())
    assert [item["code"] for item in index_list] == ["000300", "801010", "H30533"]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert set(manifest["artifacts"]) == {"index_list.json", "000300.json", "801010.json", "CHINA_INTL.json"}


def test_main_entrypoint_is_defined_and_importable_without_streamlit_server():
    source = Path(__file__).parents[1] / "app/main.py"
    tree = __import__("ast").parse(source.read_text())
    main_defs = [node for node in tree.body if isinstance(node, __import__("ast").FunctionDef) and node.name == "main"]
    assert len(main_defs) == 1
    assert any(
        isinstance(node, __import__("ast").If)
        and isinstance(node.test, __import__("ast").Compare)
        and any(isinstance(child, __import__("ast").Call) and getattr(child.func, "id", None) == "main" for child in node.body)
        for node in tree.body
    )


def test_h30533_metadata_has_canonical_delivery_fields(tmp_path, monkeypatch):
    class EmptyCache:
        def load_h30533_snapshot(self, code):
            return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

    monkeypatch.setattr(export_static_data, "OUT", tmp_path)
    monkeypatch.setattr(export_static_data, "CacheDB", EmptyCache)
    monkeypatch.setattr(export_static_data, "get_source", lambda: None)
    monkeypatch.setattr(export_static_data, "INDEX_CONFIG", {})

    export_static_data.export_all()

    entry = json.loads((tmp_path / "index_list.json").read_text())[0]
    assert set(entry) >= {"data_file", "source", "coverage", "date", "financial_as_of", "validity", "unavailable"}
    assert not {"source_type", "source_date", "validity_reason"} & set(entry)


def test_committed_static_h30533_references_are_consistent():
    root = Path(__file__).parents[1]
    index_list = json.loads((root / "static/data/index_list.json").read_text())
    h30533 = [item for item in index_list if item["code"] == "H30533"]
    assert len(h30533) == 1
    assert not [item for item in index_list if item["code"] == "CHINA_INTL"]
    assert h30533[0]["type"] == "h30533"
    assert (root / "static/data" / h30533[0]["data_file"]).exists()
    manifest = json.loads((root / "static/data/manifest.json").read_text())
    assert set(manifest["artifacts"]) >= {"index_list.json", h30533[0]["data_file"]}
