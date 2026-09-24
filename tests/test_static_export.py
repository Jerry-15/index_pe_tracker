import json

import numpy as np
import pandas as pd

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
