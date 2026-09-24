from cache.db import CacheDB


def test_successful_items_are_skipped_when_resuming(tmp_path):
    cache = CacheDB(tmp_path / "checkpoint.db")
    cache.save_checkpoint("run-1", "quotes", "600519", "success")

    checkpoint = cache.load_checkpoint("run-1", "quotes")

    assert checkpoint["600519"]["status"] == "success"
    assert checkpoint["600519"]["error_msg"] is None
    assert "600519" not in {
        key for key, value in checkpoint.items() if value["status"] != "success"
    }


def test_failed_items_are_available_for_retry(tmp_path):
    cache = CacheDB(tmp_path / "checkpoint.db")
    cache.save_checkpoint("run-1", "quotes", "600519", "failed", "source unavailable")

    checkpoint = cache.load_checkpoint("run-1", "quotes")

    assert checkpoint["600519"]["status"] == "failed"
    assert checkpoint["600519"]["error_msg"] == "source unavailable"
    assert checkpoint["600519"]["status"] != "success"


def test_clear_checkpoint_only_removes_requested_run(tmp_path):
    cache = CacheDB(tmp_path / "checkpoint.db")
    cache.save_checkpoint("run-1", "quotes", "600519", "success")
    cache.save_checkpoint("run-2", "quotes", "000001", "failed", "timeout")

    cache.clear_checkpoint("run-1")

    assert cache.load_checkpoint("run-1", "quotes") == {}
    assert cache.load_checkpoint("run-2", "quotes")["000001"]["status"] == "failed"
