import unittest

import pandas as pd

from cache.db import CacheDB
from etl.pipeline import PEETL


def _quarterly():
    return pd.DataFrame([
        {"report_date": pd.Timestamp("2024-03-31"), "quarter_profit": 10.0,
         "publish_date": pd.Timestamp("2024-04-30")},
        {"report_date": pd.Timestamp("2024-06-30"), "quarter_profit": 10.0,
         "publish_date": pd.Timestamp("2024-07-30")},
        {"report_date": pd.Timestamp("2024-09-30"), "quarter_profit": 10.0,
         "publish_date": pd.Timestamp("2024-10-30")},
        {"report_date": pd.Timestamp("2024-12-31"), "quarter_profit": 10.0,
         "publish_date": pd.Timestamp("2025-01-01")},
    ])


class CoverageGuardTests(unittest.TestCase):
    def test_loss_making_stock_market_cap_is_excluded_from_positive_pe(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "pe.db")
            etl = PEETL(cache)
            stocks = [
                {"code": "600001", "name": "profitable"},
                {"code": "600002", "name": "loss-making"},
            ]
            cache.save_quarterly_profits("600001", _quarterly())
            loss = _quarterly()
            loss["quarter_profit"] = -5.0
            cache.save_quarterly_profits("600002", loss)
            quotes = {
                code: pd.DataFrame([{
                    "date": "2025-01-02", "close": 10.0,
                    "outstanding_share": 100.0,
                }]) for code in ("600001", "600002")
            }

            result = etl._vectorized_compute_pe(stocks, quotes, allow_weekday_fallback=True)

            row = result.iloc[0]
            self.assertEqual(row["profitable_stock_count"], 1)
            self.assertEqual(row["total_market_cap"], 1000.0)
            self.assertEqual(row["total_profit_ttm"], 40.0)
            self.assertEqual(row["pe_ratio"], 25.0)
            self.assertEqual(row["total_market_cap_all"], 2000.0)
            self.assertEqual(row["total_profit_ttm_all"], 20.0)
            self.assertEqual(row["pe_ratio_all"], 100.0)
            self.assertEqual(row["loss_making_stock_count"], 1)

    def test_partial_latest_day_is_not_reported_as_valid_pe(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "pe.db")
            etl = PEETL(cache)
            codes = [f"6000{index:02d}" for index in range(20)]
            stocks = [{"code": code, "name": code} for code in codes]
            for code in codes:
                cache.save_quarterly_profits(code, _quarterly())

            quotes = {}
            for code in codes:
                rows = [
                    {"date": "2025-01-02", "close": 10.0, "outstanding_share": 100.0},
                    {"date": "2025-01-03", "close": 10.0, "outstanding_share": 100.0},
                ]
                # Only half the basket has data on the newest day.
                if code < "600010":
                    rows.append({"date": "2025-01-06", "close": 10.0, "outstanding_share": 100.0})
                quotes[code] = pd.DataFrame(rows)

            result = etl._vectorized_compute_pe(stocks, quotes, allow_weekday_fallback=True)
            result = result.sort_values("trade_date").reset_index(drop=True)

            full_days = result[result["trade_date"].isin(["2025-01-02", "2025-01-03"])]
            self.assertTrue(full_days["pe_ratio"].notna().all())

            latest = result.iloc[-1]
            self.assertEqual(latest["trade_date"], "2025-01-06")
            self.assertTrue(pd.isna(latest["pe_ratio"]))
            self.assertIn("coverage", str(latest["validity_reason"]))

    def test_invalid_rows_never_expose_a_pe_value(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheDB(Path(tmpdir) / "pe.db")
            etl = PEETL(cache)
            stocks = [{"code": "600519", "name": "A"}]
            loss_making = _quarterly()
            loss_making["quarter_profit"] = -10.0
            cache.save_quarterly_profits("600519", loss_making)
            quotes = {"600519": pd.DataFrame([
                {"date": "2025-01-02", "close": 10.0, "outstanding_share": 100.0},
            ])}

            result = etl._vectorized_compute_pe(stocks, quotes, allow_weekday_fallback=True)

            row = result.iloc[0]
            self.assertTrue(pd.isna(row["pe_ratio"]))
            self.assertIn("non_positive_profit", row["validity_reason"])


if __name__ == "__main__":
    unittest.main()
