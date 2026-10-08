from app.modules.realistic_review_ugc.scout_log import (
    RrugcScoutLogService,
    SCOUT_LOG_PURGE_INTERVAL_SECONDS,
)


def test_scout_log_retention_purge_is_throttled_per_tenant():
    with RrugcScoutLogService._purge_guard:
        RrugcScoutLogService._last_purge.clear()
    assert RrugcScoutLogService._purge_due("tenant-a", now=1000.0)
    assert not RrugcScoutLogService._purge_due("tenant-a", now=1001.0)
    assert RrugcScoutLogService._purge_due("tenant-b", now=1001.0)
    assert not RrugcScoutLogService._purge_due(
        "tenant-a", now=1000 + SCOUT_LOG_PURGE_INTERVAL_SECONDS - 1
    )
    assert RrugcScoutLogService._purge_due(
        "tenant-a", now=1000 + SCOUT_LOG_PURGE_INTERVAL_SECONDS
    )
    # Monotonic clock reset does not suppress cleanup forever.
    assert RrugcScoutLogService._purge_due("tenant-a", now=0.0)
