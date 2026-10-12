"""Replaying historical Auto Discover evidence is transactional and tenant-scoped."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.realistic_review_ugc.model import (
    RrugcScoutAgentModel, RrugcResearchSignalModel, RrugcKeywordVolumeModel,
    RrugcKeywordAgentOriginModel,
)
from app.modules.realistic_review_ugc.research_intelligence import import_signals


def test_0149_migrates_only_high_confidence_pending_dedicated_agent_evidence():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcScoutAgentModel.__table__, RrugcResearchSignalModel.__table__,
        RrugcKeywordVolumeModel.__table__, RrugcKeywordAgentOriginModel.__table__,
    ])
    with Session(engine) as session:
        session.add(RrugcScoutAgentModel(
            id="agent-1", name="Agent-Reach Auto Discover",
            tenant_id="test", token_hash="a" * 64, created_by_user_id="tester",
        ))
        session.commit()
        records = [
            {"source": "etsy", "channel": "auto-discover",
             "title": "BEST HUSBAND EVER Trucker Hat",
             "url": "https://www.etsy.com/listing/123"},
            {"source": "amazon", "channel": "auto-discover",
             "title": "Funny Caps for Women Adjustable Cotton",
             "url": "https://www.amazon.com/dp/B012345678"},
        ]
        import_signals(session, tenant_id="test", user_id="scout-agent:agent-1", signals=records)
        import_signals(session, tenant_id="other", user_id="scout-agent:agent-1", signals=records)

    path = Path(__file__).resolve().parents[5] / "database/migrations/versions/0149_rrugc_auto_keyword_replay.py"
    spec = importlib.util.spec_from_file_location("research_auto_replay", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
    with Session(engine) as session:
        approved = session.query(RrugcResearchSignalModel).filter_by(
            tenant_id="test", status="approved",
        ).all()
        assert len(approved) == 1
        assert approved[0].keyword == "BEST HUSBAND EVER"
        assert approved[0].reviewed_by_user_id.startswith("auto:agent-reach:")
        assert session.query(RrugcKeywordVolumeModel).count() == 1
        assert session.query(RrugcKeywordAgentOriginModel).count() == 1
        assert session.query(RrugcResearchSignalModel).filter_by(
            tenant_id="other", status="approved",
        ).count() == 0
        assert session.query(RrugcResearchSignalModel).filter_by(
            tenant_id="test", status="pending",
        ).count() == 1
    engine.dispose()
