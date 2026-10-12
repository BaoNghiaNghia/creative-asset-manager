from app.core.scout_log_ingress import (
    ScoutTelemetryLimiter,
    scout_log_agent_from_path,
)

AGENT = "72680eb2-3205-4b27-8a1f-66140db12f5b"
PATH = f"/api/v1/realistic-review-ugc/scout-agents/{AGENT}/logs"


def test_only_scout_log_post_is_throttled():
    assert scout_log_agent_from_path("POST", PATH) == AGENT
    assert scout_log_agent_from_path("GET", PATH) is None
    assert scout_log_agent_from_path("POST", PATH.replace("/logs", "/heartbeat")) is None
    assert scout_log_agent_from_path("POST", "/api/v1/auth/identity") is None
    assert scout_log_agent_from_path("POST", "/api/v1/realistic-review-ugc/stage4/jobs/a/retry") is None
    assert scout_log_agent_from_path("POST", "/api/v1/realistic-review-ugc/scout-agents/untrusted/logs") is None


def test_burst_capacity_and_retry_after_refill():
    limiter = ScoutTelemetryLimiter(rate=2, burst=3)
    assert [limiter.admit(AGENT, now=100.0) for _ in range(5)] == [
        True, True, True, False, False,
    ]
    assert not limiter.admit(AGENT, now=100.49)
    assert limiter.admit(AGENT, now=100.5)
    assert not limiter.admit(AGENT, now=100.5)
    assert limiter.admit(AGENT, now=101.0)


def test_flooded_agent_does_not_starve_independent_agent():
    limiter = ScoutTelemetryLimiter(rate=1, burst=1)
    other = "b60675fa-0c01-4b0f-bdae-eb02d5d9fa09"
    assert limiter.admit(AGENT, now=1.0)
    assert not limiter.admit(AGENT, now=1.0)
    assert limiter.admit(other, now=1.0)
    assert not limiter.admit(other, now=1.0)
    assert limiter.admit(AGENT, now=2.0)
