import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import export_static_data
from scripts.export_static_data import _to_rows


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


def test_export_all_only_exports_h30533_snapshot(tmp_path, monkeypatch):
    class Cache:
        def load_h30533_snapshot(self, code):
            return {"daily": pd.DataFrame(), "constituents": pd.DataFrame(), "securities": pd.DataFrame()}

        def load_pe_daily(self, code):
            raise AssertionError("legacy index data must not be exported")

    monkeypatch.setattr(export_static_data, "OUT", tmp_path)
    monkeypatch.setattr(export_static_data, "CacheDB", Cache)
    monkeypatch.setattr(export_static_data, "get_source", lambda: (_ for _ in ()).throw(AssertionError("legacy source must not be used")))
    monkeypatch.setattr(export_static_data, "INDEX_CONFIG", {"Legacy": {"code": "000300", "name": "Legacy"}})

    export_static_data.export_all()

    index_list = json.loads((tmp_path / "index_list.json").read_text())
    assert [item["code"] for item in index_list] == ["H30533"]
    assert set(p.name for p in tmp_path.glob("*.json")) == {"CHINA_INTL.json", "index_list.json", "manifest.json"}


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
