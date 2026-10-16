import pandas as pd
import pytest

from cache.db import CacheDB


@pytest.fixture
def ttm_fixture(tmp_path):
    cache = CacheDB(tmp_path / "test.db")
    financials = pd.DataFrame(
        [
            ("2024-03-31", 100.0, "2024-04-30"),
            ("2024-06-30", 240.0, "2024-08-30"),
            ("2024-09-30", 390.0, "2024-10-30"),
            ("2024-12-31", 560.0, "2025-03-28"),
            ("2025-03-31", 130.0, "2025-04-29"),
            ("2025-06-30", 290.0, "2025-08-29"),
        ],
        columns=["report_date", "net_profit_attr", "publish_date"],
    )
    financials["report_date"] = pd.to_datetime(financials["report_date"])
    financials["publish_date"] = pd.to_datetime(financials["publish_date"])
    cache.save_financials("600519", financials)
    return cache, financials
