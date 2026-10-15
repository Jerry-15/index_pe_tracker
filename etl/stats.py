"""Shared valuation history statistics."""
import numpy as np
import pandas as pd


STAT_COLUMNS = ["pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile"]


def add_valuation_stats(pe_df: pd.DataFrame) -> pd.DataFrame:
    """Add rolling valuation bands and full-series percentile to any PE frame."""
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
    window = min(500, len(result))
    min_periods = min(60, window)
    result["pe_mean"] = valid.rolling(window=window, min_periods=min_periods).mean()
    result["pe_std"] = valid.rolling(window=window, min_periods=min_periods).std().fillna(0)
    result["pe_high_line"] = result["pe_mean"] + result["pe_std"]
    result["pe_low_line"] = result["pe_mean"] - result["pe_std"]
    values = valid.dropna().sort_values().values
    result["pe_percentile"] = valid.map(
        lambda value: np.searchsorted(values, value, side="left") / len(values) * 100
        if pd.notna(value) else np.nan
    )
    return result
