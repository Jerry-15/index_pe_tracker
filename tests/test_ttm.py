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

    result = etl._vectorized_compute_pe(stocks, quotes, allow_weekday_fallback=True)

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
        allow_weekday_fallback=True,
    )

    assert result.loc[0, "total_profit_ttm"] == 0
    assert result.loc[0, "pe_ratio"] != result.loc[0, "pe_ratio"]
    assert result.loc[0, "validity_reason"] == "insufficient_coverage;non_positive_profit;pe_out_of_bounds"


def test_pe_uses_trading_calendar_intersection(tmp_path):
    from cache.db import CacheDB

    cache = CacheDB(tmp_path / "pe.db")
    etl = PEETL(cache)
    etl.source.get_trading_dates = lambda **kwargs: pd.DataFrame(
        {"date": pd.to_datetime(["2025-01-02"])}
    )
    quotes = {"600519": pd.DataFrame([
        {"date": "2025-01-01", "close": 10.0, "outstanding_share": 100.0},
        {"date": "2025-01-02", "close": 11.0, "outstanding_share": 100.0},
    ])}

    result = etl._vectorized_compute_pe([{"code": "600519", "name": "A"}], quotes)

    assert result["trade_date"].tolist() == ["2025-01-02"]


def test_calendar_failure_is_not_replaced_by_weekdays(tmp_path):
    from cache.db import CacheDB

    cache = CacheDB(tmp_path / "pe.db")
    etl = PEETL(cache)
    etl.source.get_trading_dates = lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("calendar unavailable")
    )
    quotes = {"600519": pd.DataFrame([
        {"date": "2025-01-02", "close": 10.0, "outstanding_share": 100.0},
    ])}

    try:
        etl._vectorized_compute_pe([{"code": "600519", "name": "A"}], quotes)
    except RuntimeError as exc:
        assert "calendar unavailable" in str(exc)
    else:
        raise AssertionError("calendar failure must be visible in production mode")


def test_publish_date_after_trade_date_is_excluded(tmp_path):
    from cache.db import CacheDB

    cache = CacheDB(tmp_path / "pe.db")
    etl = PEETL(cache)
    etl.source.get_trading_dates = lambda **kwargs: pd.DataFrame(
        {"date": pd.to_datetime(["2025-01-02", "2025-01-03"])}
    )
    cache.save_quarterly_profits("600519", pd.DataFrame([
        {
            "report_date": pd.Timestamp("2024-09-30"),
            "quarter_profit": 50.0,
            "publish_date": pd.Timestamp("2025-01-01"),
        },
        {
            "report_date": pd.Timestamp("2024-12-31"),
            "quarter_profit": 100.0,
            "publish_date": pd.Timestamp("2025-01-03"),
        },
    ]))
    quotes = {"600519": pd.DataFrame([
        {"date": "2025-01-02", "close": 10.0, "outstanding_share": 100.0},
        {"date": "2025-01-03", "close": 10.0, "outstanding_share": 100.0},
    ])}

    result = etl._vectorized_compute_pe([{"code": "600519", "name": "A"}], quotes)

    assert result.loc[result["trade_date"] == "2025-01-02", "profitable_stock_count"].item() == 0
    assert result.loc[result["trade_date"] == "2025-01-03", "profitable_stock_count"].item() == 1


def test_old_pe_daily_schema_migrates_validity_columns(tmp_path):
    import sqlite3
    from cache.db import CacheDB

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE index_pe_daily (
                trade_date TEXT NOT NULL,
                index_code TEXT NOT NULL,
                total_market_cap REAL,
                total_profit_ttm REAL,
                pe_ratio REAL,
                stock_count INTEGER,
                updated_at REAL,
                PRIMARY KEY (trade_date, index_code)
            )
        """)

    CacheDB(path)
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(index_pe_daily)")}

    assert {"valid_stock_count", "profitable_stock_count", "coverage_ratio", "validity_reason"} <= columns
