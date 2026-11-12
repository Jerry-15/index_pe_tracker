import unittest

import pandas as pd

from etl.stats import add_valuation_stats


class StatsWindowConsistencyTests(unittest.TestCase):
    """The trend bands and the percentile must share one lookback window."""

    def _frame(self, values):
        return pd.DataFrame({
            "trade_date": pd.date_range("2015-01-01", periods=len(values), freq="B").strftime("%Y-%m-%d"),
            "pe_ratio": values,
        })

    def test_percentile_ignores_regimes_outside_the_rolling_window(self):
        # 500 days of very low PE, then 2000 days around 20, ending at 12.
        # A full-history percentile would put 12 near the 20th percentile,
        # but inside the trailing window 12 is the lowest value seen.
        values = [5.0] * 500 + [20.0] * 1999 + [12.0]
        out = add_valuation_stats(self._frame(values), window=2000)

        latest = out.iloc[-1]
        self.assertLess(latest["pe_ratio"], latest["pe_low_line"])
        self.assertLess(latest["pe_percentile"], 20.0)

    def test_below_low_line_never_reports_high_percentile(self):
        # Steady regime then a sharp drop: the two tabs must agree.
        values = [18.0, 22.0] * 1000 + [10.0]
        out = add_valuation_stats(self._frame(values), window=2000)

        latest = out.iloc[-1]
        self.assertLess(latest["pe_ratio"], latest["pe_low_line"])
        self.assertLess(latest["pe_percentile"], 20.0)

    def test_above_high_line_never_reports_low_percentile(self):
        values = [18.0, 22.0] * 1000 + [30.0]
        out = add_valuation_stats(self._frame(values), window=2000)

        latest = out.iloc[-1]
        self.assertGreater(latest["pe_ratio"], latest["pe_high_line"])
        self.assertGreater(latest["pe_percentile"], 80.0)


if __name__ == "__main__":
    unittest.main()
