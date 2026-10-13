from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
from app.modules.realistic_review_ugc.model import (
    RrugcResearchSignalModel, RrugcScoutAgentModel,
)
from app.modules.realistic_review_ugc.research_discovery import (
    AGENT_NAME, discovery_status, set_discovery_pause,
)


def test_server_discovery_is_tenant_scoped_and_defaults_to_unconfigured():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcResearchSignalModel.__table__, RrugcScoutAgentModel.__table__,
    ])
    with Session(engine) as session:
        assert discovery_status(session, "tenant-a")["state"] == "not_configured"
        session.add(RrugcScoutAgentModel(
            id="00000000-0000-0000-0000-000000000099",
            tenant_id="tenant-a", name=AGENT_NAME, token_hash="x"*64,
            status="busy", active=True, created_by_user_id="operator",
            last_seen_at=datetime.now(timezone.utc),
        ))
        session.add(RrugcResearchSignalModel(
            id="00000000-0000-0000-0000-000000000101",
            tenant_id="tenant-a", fingerprint="a"*64, source="etsy",
            channel="auto-discover", url="https://www.etsy.com/listing/123456",
            title="Cap", excerpt="Search results", status="pending",
            created_by_user_id="scout-agent",
        ))
        session.commit()
        own = discovery_status(session, "tenant-a")
        assert own["state"] == "busy"
        assert own["total_evidence"] == own["pending_review"] == 1
        assert own["platforms"] == {"pinterest": 0, "amazon": 0, "etsy": 1}
        other = discovery_status(session, "tenant-b")
        assert other["state"] == "not_configured"
        assert other["total_evidence"] == other["pending_review"] == 0
        assert set_discovery_pause(session, "tenant-a", True)["state"] == "paused"
        assert discovery_status(session, "tenant-b")["state"] == "not_configured"
        assert set_discovery_pause(session, "tenant-a", False)["state"] == "waiting"
        assert discovery_status(session, "tenant-a")["connected"] is True
        assert session.get(RrugcScoutAgentModel,
                           "00000000-0000-0000-0000-000000000099").status == "offline"
        try:
            set_discovery_pause(session, "tenant-b", True)
        except LookupError:
            pass
        else:
            raise AssertionError("Must not create or control another tenant's agent")
    engine.dispose()


def test_stale_server_heartbeat_never_reports_running():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcResearchSignalModel.__table__, RrugcScoutAgentModel.__table__,
    ])
    with Session(engine) as session:
        session.add(RrugcScoutAgentModel(
            id="00000000-0000-0000-0000-000000000102",
            tenant_id="tenant", name=AGENT_NAME, token_hash="y"*64,
            status="busy", active=True, created_by_user_id="operator",
            last_seen_at=datetime.now(timezone.utc) - timedelta(hours=4),
        ))
        session.commit()
        assert discovery_status(session, "tenant")["state"] == "offline"
        assert discovery_status(session, "tenant")["heartbeat_age_seconds"] > 3 * 3600
        assert set_discovery_pause(session, "tenant", True)["state"] == "paused"
    engine.dispose()
