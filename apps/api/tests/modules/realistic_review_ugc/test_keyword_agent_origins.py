"""Evidence is deduplicated per exact source but never across distinct agents."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.realistic_review_ugc.keyword_agent_origins import record_keyword_agent, origins_for_keywords
from app.modules.realistic_review_ugc.model import RrugcKeywordAgentOriginModel


def test_agent_provenance_is_tenant_scoped_and_keeps_multiple_agents_and_sources():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RrugcKeywordAgentOriginModel.__table__])
    try:
        with Session(engine) as session:
            kwargs = dict(tenant_id="tenant-a", keyword_normalized="made to worship",
                          source_platform="pinterest", source_channel="keyword-scout",
                          source_url="https://www.pinterest.com/pin/123456")
            assert record_keyword_agent(session, agent_key="scout:one",
                                        agent_name="Keyword Scout Windows", **kwargs)
            # Retry of the same pin does NOT inflate analytics.
            assert not record_keyword_agent(session, agent_key="scout:one",
                                            agent_name="Keyword Scout Windows", **kwargs)
            assert record_keyword_agent(session, agent_key="scout:two",
                                        agent_name="Other Keyword Scout", **kwargs)
            assert record_keyword_agent(
                session, tenant_id="tenant-a", keyword_normalized="made to worship",
                agent_key="scout:one", agent_name="Keyword Scout Windows",
                source_platform="etsy", source_channel="auto-discover",
                source_url="https://www.etsy.com/listing/12345",
            )
            assert record_keyword_agent(session, agent_key="scout:one",
                                        agent_name="Keyword Scout Windows",
                                        **(kwargs | {"tenant_id": "tenant-b"}))
            session.commit()
            a = origins_for_keywords(session, tenant_id="tenant-a", keywords=["made to worship"])
            b = origins_for_keywords(session, tenant_id="tenant-b", keywords=["made to worship"])
            assert len(a["made to worship"]) == 2
            assert len(b["made to worship"]) == 1
            assert a["made to worship"][0] == {
                "agent_key": "scout:one", "agent_name": "Keyword Scout Windows",
                "platforms": ["pinterest", "etsy"], "evidence_count": 2,
            }
            assert "unknown quote" not in a
    finally:
        engine.dispose()


def test_invalid_agent_names_do_not_record_attribution():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RrugcKeywordAgentOriginModel.__table__])
    try:
        with Session(engine) as session:
            assert not record_keyword_agent(
                session, tenant_id="tenant-a", keyword_normalized="abc def",
                agent_key="", agent_name="Guess", source_platform="unknown",
                source_channel="unknown")
            assert origins_for_keywords(session, tenant_id="tenant-a", keywords=["abc def"]) == {}
    finally:
        engine.dispose()
