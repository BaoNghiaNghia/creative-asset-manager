"""Evidence is deduplicated per exact source but never across distinct agents."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.realistic_review_ugc.keyword_agent_origins import record_keyword_agent, origins_for_keywords
from app.modules.realistic_review_ugc.model import RrugcKeywordAgentOriginModel, RrugcResearchSignalModel


def test_agent_provenance_is_tenant_scoped_and_keeps_multiple_agents_and_sources():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RrugcKeywordAgentOriginModel.__table__, RrugcResearchSignalModel.__table__])
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
                "sources": [
                    {"platform": "pinterest", "url": "https://www.pinterest.com/pin/123456", "channel": "keyword-scout", "title": None, "excerpt": None},
                    {"platform": "etsy", "url": "https://www.etsy.com/listing/12345", "channel": "auto-discover", "title": None, "excerpt": None},
                ],
            }
            assert "unknown quote" not in a
    finally:
        engine.dispose()




def test_approved_research_metadata_is_returned_only_for_correct_tenant_and_keyword():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RrugcKeywordAgentOriginModel.__table__, RrugcResearchSignalModel.__table__])
    url = "https://www.etsy.com/listing/999999"
    with Session(engine) as session:
        record_keyword_agent(
            session, tenant_id="my-tenant", keyword_normalized="best husband ever",
            agent_key="scout:reach", agent_name="Agent-Reach Auto Discover",
            source_platform="etsy", source_channel="auto-discover", source_url=url,
        )
        session.add_all([
            RrugcResearchSignalModel(
                tenant_id="my-tenant", fingerprint="1"*64, source="etsy",
                channel="auto-discover", url=url,
                title="Best Husband Ever Trucker Hat",
                excerpt="Listing phrase detected by search; no photo verification.",
                status="approved", keyword="BEST HUSBAND EVER",
                created_by_user_id="scout-agent:reach",
            ),
            RrugcResearchSignalModel(
                tenant_id="other-tenant", fingerprint="2"*64, source="etsy",
                channel="auto-discover", url=url,
                title="Private Other Tenant Listing",
                excerpt="Do not leak",
                status="approved", keyword="BEST HUSBAND EVER",
                created_by_user_id="scout-agent:other",
            ),
            RrugcResearchSignalModel(
                tenant_id="my-tenant", fingerprint="3"*64, source="etsy",
                channel="auto-discover", url="https://www.etsy.com/listing/999998",
                title="Unrelated Keyword Listing",
                excerpt="Not part of this quote",
                status="approved", keyword="OTHER KEYWORD",
                created_by_user_id="scout-agent:reach",
            ),
        ])
        session.commit()
        results = origins_for_keywords(session, tenant_id="my-tenant", keywords=["best husband ever"])
        assert results["best husband ever"][0]["sources"] == [{
            "platform": "etsy", "url": url, "channel": "auto-discover",
            "title": "Best Husband Ever Trucker Hat",
            "excerpt": "Listing phrase detected by search; no photo verification.",
        }]
        assert origins_for_keywords(session, tenant_id="other-tenant", keywords=["best husband ever"]) == {}
    engine.dispose()

def test_invalid_agent_names_do_not_record_attribution():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RrugcKeywordAgentOriginModel.__table__, RrugcResearchSignalModel.__table__])
    try:
        with Session(engine) as session:
            assert not record_keyword_agent(
                session, tenant_id="tenant-a", keyword_normalized="abc def",
                agent_key="", agent_name="Guess", source_platform="unknown",
                source_channel="unknown")
            assert origins_for_keywords(session, tenant_id="tenant-a", keywords=["abc def"]) == {}
    finally:
        engine.dispose()
