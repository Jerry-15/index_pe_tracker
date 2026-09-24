from cache.db import CacheDB


def test_successful_items_are_skipped_when_resuming(tmp_path):
    cache = CacheDB(tmp_path / "checkpoint.db")
    cache.save_checkpoint("run-1", "quotes", "600519", "success")
    cache.save_checkpoint("run-1", "quotes", "000001", "failed", "source unavailable")

    checkpoint = cache.load_checkpoint("run-1", "quotes")
    work_items = [
        item_key
        for item_key, record in checkpoint.items()
        if record["status"] != "success"
    ]

    assert work_items == ["000001"]


def test_failed_items_are_available_for_retry(tmp_path):
    cache = CacheDB(tmp_path / "checkpoint.db")
    cache.save_checkpoint("run-1", "quotes", "600519", "failed", "source unavailable")

    checkpoint = cache.load_checkpoint("run-1", "quotes")
    work_items = [
        item_key
        for item_key, record in checkpoint.items()
        if record["status"] != "success"
    ]
    assert work_items == ["600519"]

    # Stage-specific item keys (stock codes here) are the caller's responsibility.
    cache.save_checkpoint("run-1", "quotes", work_items[0], "success")
    resumed_checkpoint = cache.load_checkpoint("run-1", "quotes")

    assert checkpoint["600519"]["error_msg"] == "source unavailable"
    assert resumed_checkpoint["600519"]["status"] == "success"
    assert resumed_checkpoint["600519"]["error_msg"] is None


def test_clear_checkpoint_only_removes_requested_run(tmp_path):
    cache = CacheDB(tmp_path / "checkpoint.db")
    cache.save_checkpoint("run-1", "quotes", "600519", "success")
    cache.save_checkpoint("run-2", "quotes", "000001", "failed", "timeout")

    cache.clear_checkpoint("run-1")

    assert cache.load_checkpoint("run-1", "quotes") == {}
    assert cache.load_checkpoint("run-2", "quotes")["000001"]["status"] == "failed"
