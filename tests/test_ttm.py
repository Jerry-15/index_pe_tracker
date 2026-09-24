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
    assert abs(decomposed - standard) / standard < 0.01


def test_ttm_methods_return_none_when_reports_are_insufficient(ttm_fixture):
    cache, financials = ttm_fixture
    etl = PEETL(cache)
    insufficient = financials.iloc[[0]].copy()
    quarterly = etl._decompose_quarterly(insufficient)
    target = pd.Timestamp("2024-09-01")

    assert etl._compute_ttm_from_quarters(quarterly, target) is None
    assert ttm_by_standard_formula(insufficient, target) is None
