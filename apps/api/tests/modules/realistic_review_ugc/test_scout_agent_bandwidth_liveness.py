"""Regression: four active Windows Scout profiles must not appear offline
after missing one heartbeat during Pinterest thumbnail congestion."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.modules.realistic_review_ugc.scout_automation import (
    SCOUT_OFFLINE_SECONDS, SCOUT_LEASE_SECONDS, effective_agent_status,
)


def test_agent_liveness_tolerates_two_missed_20_second_heartbeats():
    now = datetime.now(timezone.utc)
    assert SCOUT_OFFLINE_SECONDS >= 80
    assert SCOUT_LEASE_SECONDS >= 15 * 60
    agent = SimpleNamespace(active=True, status="busy",
                            last_seen_at=now - timedelta(seconds=64))
    assert effective_agent_status(agent, now=now) == "busy"


def test_long_disconnection_and_revocation_remain_offline():
    now = datetime.now(timezone.utc)
    agent = SimpleNamespace(active=True, status="busy",
                            last_seen_at=now - timedelta(seconds=SCOUT_OFFLINE_SECONDS + 1))
    assert effective_agent_status(agent, now=now) == "offline"
    agent.last_seen_at = now
    agent.active = False
    assert effective_agent_status(agent, now=now) == "offline"
