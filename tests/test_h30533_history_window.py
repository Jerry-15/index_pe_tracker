import unittest

from etl.pipeline_us import USETL


class H30533HistoryWindowTests(unittest.TestCase):
    def test_historical_series_uses_near_eight_year_window(self):
        dates = [f"{year:04d}-01-01" for year in range(2100)]
        selected = USETL._select_historical_dates(dates)

        self.assertEqual(len(selected), 2000)
        self.assertEqual(selected[0], dates[100])
