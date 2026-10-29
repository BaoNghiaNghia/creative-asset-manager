"""G3 queue decision policy tests: no real worker or provider usage."""
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.realistic_review_ugc.typography_analysis_policy import analysis_decision


def arguments(**values):
    defaults = dict(enabled=True, tenant_allowed=True, rights="licensed",
                    editorial_status="candidate", status="pending",
                    attempts=0, active_jobs=0)
    return {**defaults, **values}


def test_default_off_and_tenant_gate():
    assert analysis_decision(**arguments(enabled=False)).action == "skip"
    assert analysis_decision(**arguments(tenant_allowed=False)).action == "skip"


@pytest.mark.parametrize("rights", ["unknown", "blocked", "", "unverified"])
def test_rights_block_any_dispatch_even_with_cache_hit(rights):
    result = analysis_decision(**arguments(rights=rights, cache_hit=True))
    assert result.action == "manual_review"


@pytest.mark.parametrize("editorial", ["archived", "rejected"])
def test_inactive_reference_never_dispatches(editorial):
    assert analysis_decision(**arguments(editorial_status=editorial)).action == "skip"


def test_cache_hit_avoids_job_and_running_does_not_requeue():
    assert analysis_decision(**arguments(cache_hit=True)).action == "cache_hit"
    assert analysis_decision(**arguments(status="running")).action == "skip"


def test_capacity_and_bounded_retry():
    assert analysis_decision(**arguments(active_jobs=1)).action == "backoff"
    assert analysis_decision(**arguments(active_jobs=1, concurrency=2)).action == "dispatch"
    assert analysis_decision(**arguments(attempts=3)).action == "exhausted"
    assert analysis_decision(**arguments(attempts=30)).action == "exhausted"


def test_retry_backoff_is_bounded_and_timestamped():
    now = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)
    last = now - timedelta(seconds=40)
    assert analysis_decision(**arguments(status="failed", attempts=1, last_failed_at=last, now=now)).action == "dispatch"
    retry = analysis_decision(**arguments(status="failed", attempts=2, last_failed_at=last, now=now))
    assert retry.action == "backoff"
    assert retry.reason == "retry_cooldown"
    assert retry.next_attempt_at == last + timedelta(seconds=120)


@pytest.mark.parametrize("concurrency", [0, 3, -1])
def test_reject_invalid_concurrency(concurrency):
    with pytest.raises(ValueError):
        analysis_decision(**arguments(concurrency=concurrency))


def test_human_review_and_invalid_attempt_counts():
    assert analysis_decision(**arguments(status="needs_review")).action == "manual_review"
    with pytest.raises(ValueError):
        analysis_decision(**arguments(attempts=-1))
