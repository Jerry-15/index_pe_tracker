"""Shared valuation result contract for all index-like products."""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import CONSTITUENT_BASIS, MARKET_CAP_METHOD, ROLLING_WINDOW


def normalize_result(frame: pd.DataFrame, index_code: str, constituent_count=None) -> pd.DataFrame:
    """Normalize market-specific valuation rows into the common index contract."""
    result = frame.copy()
    result["index_code"] = index_code
    result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y-%m-%d")
    if "stock_count" not in result:
        result["stock_count"] = result.get("valid_count", 0)
    if "valid_stock_count" not in result:
        result["valid_stock_count"] = result["valid_count"] if "valid_count" in result else result["stock_count"]
    if "constituent_count" not in result:
        result["constituent_count"] = constituent_count
    if "coverage_ratio" not in result:
        if "weight_coverage" in result:
            result["coverage_ratio"] = result["weight_coverage"]
        else:
            result["coverage_ratio"] = result["valid_stock_count"] / result["constituent_count"]
    if "quote_valid_count" not in result:
        result["quote_valid_count"] = result["valid_stock_count"]
    if "financial_valid_count" not in result:
        result["financial_valid_count"] = result["valid_stock_count"]
    if "profitable_stock_count" not in result:
        result["profitable_stock_count"] = result["valid_stock_count"]
    if "market_cap_method" not in result:
        result["market_cap_method"] = MARKET_CAP_METHOD
    if "constituent_basis" not in result:
        result["constituent_basis"] = CONSTITUENT_BASIS
    if "data_source" not in result:
        result["data_source"] = result.get("source_type")
    if "validity_reason" not in result:
        result["validity_reason"] = None
    return result


def compute_statistics(frame: pd.DataFrame) -> pd.DataFrame:
    """Delegate to the single shared statistics implementation."""
    from etl.stats import add_valuation_stats
    return add_valuation_stats(frame)
