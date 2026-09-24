import json

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


def test_static_export_writes_strict_json_files(tmp_path, monkeypatch):
    class FakeCache:
        def load_pe_daily(self, code):
            return pd.DataFrame([{
                "trade_date": "2025-01-02", "pe_ratio": np.nan,
                "total_market_cap": 1000.0, "total_profit_ttm": 0.0,
                "valid_stock_count": 1, "profitable_stock_count": 0,
                "coverage_ratio": 0.5,
                "validity_reason": "non_positive_profit;pe_out_of_bounds",
            }])

        def load_pe_stats(self, code):
            return pd.DataFrame()

    class FakeSource:
        def get_industry_reference(self):
            return pd.DataFrame()

        def get_index_pe_history(self, name):
            return pd.DataFrame()

    monkeypatch.setattr(export_static_data, "OUT", tmp_path)
    monkeypatch.setattr(export_static_data, "CacheDB", FakeCache)
    monkeypatch.setattr(export_static_data, "get_source", lambda: FakeSource())
    monkeypatch.setattr(export_static_data, "INDEX_CONFIG", {
        "Test": {"code": "000300", "name": "Test"},
    })

    export_static_data.export_all()

    output = json.loads((tmp_path / "000300.json").read_text())
    index_list = json.loads((tmp_path / "index_list.json").read_text())
    assert output[0][1] is None
    assert output[0][8:] == [1, 0, 0.5, "non_positive_profit;pe_out_of_bounds"]
    assert index_list[0]["validity_reason"] == "non_positive_profit;pe_out_of_bounds"
