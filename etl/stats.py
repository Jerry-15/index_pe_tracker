"""Shared valuation history statistics."""
import numpy as np
import pandas as pd

from config import ROLLING_WINDOW

STAT_COLUMNS = ["pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile"]


def add_valuation_stats(pe_df: pd.DataFrame, window: int | None = None) -> pd.DataFrame:
    """Add rolling valuation bands and a rolling percentile.

    Both the bands and the percentile use the *same* trailing window, so the
    trend tab and the percentile tab can never disagree about whether the
    current PE is cheap or expensive.
    """
    result = pe_df.copy()
    if result.empty:
        for column in STAT_COLUMNS:
            result[column] = pd.Series(dtype=float)
        return result
    result = result.sort_values("trade_date").reset_index(drop=True)
    for column in STAT_COLUMNS:
        result[column] = np.nan
    valid = pd.to_numeric(result.get("pe_ratio", pd.Series(dtype=float)), errors="coerce")
    if valid.notna().sum() == 0:
        return result
    size = min(window or ROLLING_WINDOW, len(result))
    min_periods = max(1, min(size, max(60, size // 2)))
    result["pe_mean"] = valid.rolling(window=size, min_periods=min_periods).mean()
    result["pe_std"] = valid.rolling(window=size, min_periods=min_periods).std().fillna(0)
    result["pe_high_line"] = result["pe_mean"] + result["pe_std"]
    result["pe_low_line"] = result["pe_mean"] - result["pe_std"]
    result["pe_percentile"] = (
        valid.rolling(window=size, min_periods=min_periods).rank(pct=True) * 100
    )
    return result
