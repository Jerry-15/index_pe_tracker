import pandas as pd

from etl.pipeline import PEETL
from scripts.validate_pe import ttm_by_standard_formula


def test_same_year_cumulative_reports_are_decomposed_into_quarters(ttm_fixture):
    cache, financials = ttm_fixture
    quarterly = PEETL(cache)._decompose_quarterly(financials)
    profits = quarterly.set_index("report_date")["quarter_profit"]

    assert profits[pd.Timestamp("2024-03-31")] == 100.0
    assert profits[pd.Timestamp("2024-06-30")] == 140.0
    assert profits[pd.Timestamp("2024-09-30")] == 150.0
    assert profits[pd.Timestamp("2024-12-31")] == 170.0
    assert profits[pd.Timestamp("2025-03-31")] == 130.0
    assert profits[pd.Timestamp("2025-06-30")] == 160.0


def test_standard_formula_matches_decomposed_quarter_ttm(ttm_fixture):
    cache, financials = ttm_fixture
    etl = PEETL(cache)
    quarterly = etl._decompose_quarterly(financials)
    target = pd.Timestamp("2025-09-01")

    decomposed = etl._compute_ttm_from_quarters(quarterly, target)
    standard = ttm_by_standard_formula(financials, target)

    assert decomposed is not None
    assert standard is not None
    assert abs(decomposed - standard) < 1e-9


def test_ttm_methods_return_none_when_reports_are_insufficient(ttm_fixture):
    cache, financials = ttm_fixture
    etl = PEETL(cache)
    insufficient = financials.iloc[[0]].copy()
    quarterly = etl._decompose_quarterly(insufficient)
    target = pd.Timestamp("2024-09-01")

    assert etl._compute_ttm_from_quarters(quarterly, target) is None
    assert ttm_by_standard_formula(insufficient, target) is None


def test_missing_same_year_cumulative_period_is_not_replaced_by_prior_year(ttm_fixture):
    cache, financials = ttm_fixture
    missing_h1 = pd.concat(
        [
            financials[financials["report_date"] != pd.Timestamp("2025-06-30")],
            pd.DataFrame(
                [{"report_date": pd.Timestamp("2025-09-30"), "net_profit_attr": 450.0,
                  "publish_date": pd.Timestamp("2025-10-30")}]
            ),
        ],
        ignore_index=True,
    )

    quarterly = PEETL(cache)._decompose_quarterly(missing_h1)

    assert pd.Timestamp("2025-09-30") not in set(quarterly["report_date"])


def test_pe_rows_include_invalidity_metadata_for_insufficient_coverage(tmp_path):
    from cache.db import CacheDB

    cache = CacheDB(tmp_path / "pe.db")
    etl = PEETL(cache)
    stocks = [{"code": "600519", "name": "A"}, {"code": "000001", "name": "B"}]
    quotes = {
        "600519": pd.DataFrame([{"date": "2025-01-02", "close": 10.0, "outstanding_share": 100.0}]),
        "000001": pd.DataFrame([{"date": "2025-01-02", "close": None, "outstanding_share": 100.0}]),
    }
    quarterly = pd.DataFrame(
        [{"report_date": pd.Timestamp("2024-03-31"), "quarter_profit": 10.0,
          "publish_date": pd.Timestamp("2024-04-30")}]
    )
    cache.save_quarterly_profits("600519", quarterly)

    result = etl._vectorized_compute_pe(stocks, quotes)

    assert len(result) == 1
    assert result.loc[0, "valid_stock_count"] == 1
    assert result.loc[0, "profitable_stock_count"] == 0
    assert result.loc[0, "coverage_ratio"] == 0
    assert result.loc[0, "validity_reason"] == "insufficient_coverage;non_positive_profit;pe_out_of_bounds"


def test_pe_rows_explain_missing_positive_ttm(tmp_path):
    from cache.db import CacheDB

    cache = CacheDB(tmp_path / "pe.db")
    result = PEETL(cache)._vectorized_compute_pe(
        [{"code": "600519", "name": "A"}],
        {"600519": pd.DataFrame([{"date": "2025-01-02", "close": 10.0,
                                   "outstanding_share": 100.0}])},
    )

    assert result.loc[0, "total_profit_ttm"] == 0
    assert result.loc[0, "pe_ratio"] != result.loc[0, "pe_ratio"]
    assert result.loc[0, "validity_reason"] == "insufficient_coverage;non_positive_profit;pe_out_of_bounds"
