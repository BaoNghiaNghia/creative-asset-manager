from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.realistic_review_ugc.model import RrugcResearchSignalModel, RrugcKeywordVolumeModel, RrugcKeywordAgentOriginModel, RrugcScoutAgentModel
from app.modules.realistic_review_ugc.keyword_agent_origins import origins_for_keywords
from app.modules.realistic_review_ugc.research_intelligence import (
    ResearchValidationError, import_signals, list_signals,
    normalize_keyword, review_signal, stage0_export, apply_approved_to_stage0,
    backfill_auto_discover_candidates, validate_url,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RrugcResearchSignalModel.__table__, RrugcKeywordVolumeModel.__table__, RrugcKeywordAgentOriginModel.__table__, RrugcScoutAgentModel.__table__])
    with Session(engine) as db:
        yield db
    engine.dispose()


def _signal(url="https://news.google.com/rss/articles/example"):
    return {"source": "rss", "channel": "market", "title": "<b>CAPS</b> trending",
            "url": url, "excerpt": "Hats for the weekend"}


def test_auto_approve_route_preserves_ambiguous_candidates(session):
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc.research_router import approve_clear_research_keywords
    agent_id = "00000000-0000-4000-8000-000000000099"
    session.add(RrugcScoutAgentModel(id=agent_id, tenant_id="tenant-a",
        name="Agent-Reach Auto Discover", token_hash="z"*64,
        status="ready", active=True, created_by_user_id="u"))
    session.commit()
    import_signals(session, tenant_id="tenant-a", user_id="scout-agent:" + agent_id, signals=[
        {"source": "etsy", "channel": "auto-discover", "title": "Made to Worship Embroidered Trucker Hat", "url": "https://www.etsy.com/listing/123456789"},
        {"source": "etsy", "channel": "auto-discover", "title": "Baseball Hat, Custom Gift For Everyone", "url": "https://www.etsy.com/listing/123456790"},
    ])
    result = approve_clear_research_keywords(session=session, principal=SimpleNamespace(active_tenant_id="tenant-a"))
    assert result == {"checked": 2, "auto_promoted": 1, "needs_review": 1}
    assert session.query(RrugcKeywordVolumeModel).filter_by(tenant_id="tenant-a").count() == 1


def test_research_signals_are_tenant_scoped_and_require_review(session):
    item = _signal()
    assert import_signals(session, tenant_id="tenant-a", user_id="user-a", signals=[item]) == {
        "added": 1, "duplicates": 0}
    assert import_signals(session, tenant_id="tenant-a", user_id="user-a", signals=[item]) == {
        "added": 0, "duplicates": 1}
    assert import_signals(session, tenant_id="tenant-b", user_id="user-b", signals=[item]) == {
        "added": 1, "duplicates": 0}
    a = list_signals(session, tenant_id="tenant-a")
    b = list_signals(session, tenant_id="tenant-b")
    assert len(a) == len(b) == 1
    assert a[0]["id"] != b[0]["id"]
    assert a[0]["title"] == "CAPS trending"
    assert stage0_export(session, tenant_id="tenant-a") == {"keywords": [], "force": False}
    with pytest.raises(LookupError):
        review_signal(session, tenant_id="tenant-b", signal_id=a[0]["id"],
                      user_id="user-b", decision="approved", keyword="MADE TO WORSHIP")
    assert review_signal(session, tenant_id="tenant-a", signal_id=a[0]["id"],
                         user_id="user-a", decision="approved",
                         keyword="MADE TO WORSHIP")["status"] == "approved"
    assert stage0_export(session, tenant_id="tenant-a") == {
        "keywords": ["MADE TO WORSHIP"], "force": False}
    keyword_row = session.query(RrugcKeywordVolumeModel).filter_by(
        tenant_id="tenant-a", keyword_normalized="made to worship").one()
    assert keyword_row.keyword == "MADE TO WORSHIP"
    assert keyword_row.provider == "pending"
    assert keyword_row.search_volume == 0
    assert keyword_row.request_count == 0
    assert keyword_row.provider_raw_json["research_unverified"] is True
    assert session.query(RrugcKeywordVolumeModel).filter_by(tenant_id="tenant-b").count() == 0
    assert stage0_export(session, tenant_id="tenant-b") == {"keywords": [], "force": False}
    with pytest.raises(ResearchValidationError):
        review_signal(session, tenant_id="tenant-a", signal_id=a[0]["id"],
                      user_id="user-a", decision="rejected", keyword=None)


@pytest.mark.parametrize("source,links,expected", [
    ("pinterest", ["https://www.pinterest.com/pin/123456789/?ref=abc",
                   "https://pinterest.com/pin/123456789/"], "https://www.pinterest.com/pin/123456789"),
    ("etsy", ["https://etsy.com/listing/123456789/cap?utm_campaign=foo",
              "https://www.etsy.com/listing/123456789/other-name"], "https://www.etsy.com/listing/123456789"),
    ("amazon", ["https://amazon.com/gp/product/b012345678?tag=foo",
                "https://www.amazon.com/dp/B012345678"], "https://www.amazon.com/dp/B012345678"),
])
def test_marketplace_research_dedupes_listing_urls_without_any_provider_call(
        session, source, links, expected, monkeypatch):
    import httpx
    def deny(*args, **kwargs):
        raise AssertionError("Import must never fetch a marketplace product page")
    monkeypatch.setattr(httpx, "get", deny)
    signals = [
        {"source": source, "channel": "manual-listing", "title": "Cap quote listing",
         "url": url, "excerpt": "unverified operator supplied link"}
        for url in links
    ]
    assert import_signals(session, tenant_id="tenant-a", user_id="actor", signals=signals) == {
        "added": 1, "duplicates": 1}
    saved = list_signals(session, tenant_id="tenant-a")
    assert len(saved) == 1
    assert saved[0]["url"] == expected
    assert saved[0]["platform"] == source
    assert stage0_export(session, tenant_id="tenant-a") == {"keywords": [], "force": False}


@pytest.mark.parametrize("source,url", [
    ("etsy", "https://www.etsy.com.evil.example/listing/123456"),
    ("etsy", "https://www.amazon.com/dp/B012345678"),
    ("etsy", "https://www.etsy.com/search?q=trucker+hat"),
    ("amazon", "https://amazon.com.evil.example/dp/B012345678"),
    ("amazon", "https://www.amazon.com/s?k=trucker+hat"),
    ("amazon", "https://www.amazon.com/dp/ABC"),
    ("pinterest", "https://notpinterest.com/pin/123456"),
    ("pinterest", "https://www.pinterest.com/search/pins/?q=cap"),
    ("pinterest", "https://www.pinterest.com@evil.example/pin/123456"),
])
def test_marketplace_source_requires_matching_official_listing_domain(session, source, url):
    with pytest.raises(ResearchValidationError):
        import_signals(session, tenant_id="tenant-a", user_id="actor", signals=[
            {"source": source, "channel": "manual", "title": "Hat", "url": url},
        ])
    assert list_signals(session, tenant_id="tenant-a") == []


def test_batch_duplicates_do_not_reset_reviewed_signals(session):
    item = _signal()
    assert import_signals(session, tenant_id="tenant-a", user_id="u", signals=[item, item]) == {
        "added": 1, "duplicates": 1}
    first = list_signals(session, tenant_id="tenant-a")[0]
    review_signal(session, tenant_id="tenant-a", user_id="u",
                  signal_id=first["id"], decision="approved", keyword="TEXAS HATS")
    other = _signal("https://news.google.com/rss/articles/other")
    assert import_signals(session, tenant_id="tenant-a", user_id="u", signals=[
        item, other]) == {"added": 1, "duplicates": 1}
    rows = list_signals(session, tenant_id="tenant-a", status="all")
    assert len(rows) == 2
    assert next(row for row in rows if row["id"] == first["id"])["status"] == "approved"
    assert stage0_export(session, tenant_id="tenant-a")["keywords"] == ["TEXAS HATS"]


def test_stage0_apply_repairs_old_approvals_without_overwriting_existing_volume(session):
    import_signals(session, tenant_id="tenant-a", user_id="u", signals=[
        {"source": "pinterest", "channel": "auto-discover",
         "title": "Made to Worship hat", "url": "https://www.pinterest.com/pin/123456789"}
    ])
    research = list_signals(session, tenant_id="tenant-a")[0]
    # Simulates historical versions where approval did not write Stage 0.
    row = session.get(RrugcResearchSignalModel, research["id"])
    row.status, row.keyword = "approved", "MADE TO WORSHIP"
    session.commit()
    assert session.query(RrugcKeywordVolumeModel).count() == 0
    assert apply_approved_to_stage0(session, tenant_id="tenant-b") == {
        "added": 0, "existing": 0, "checked": 0}
    assert apply_approved_to_stage0(session, tenant_id="tenant-a") == {
        "added": 1, "existing": 0, "checked": 1}
    volume = session.query(RrugcKeywordVolumeModel).filter_by(
        tenant_id="tenant-a", keyword_normalized="made to worship").one()
    assert volume.source_pin_url == "https://www.pinterest.com/pin/123456789"
    volume.search_volume = 412
    volume.provider = "aebrowse_google_ads"
    volume.picked = True
    session.commit()
    assert apply_approved_to_stage0(session, tenant_id="tenant-a") == {
        "added": 0, "existing": 1, "checked": 1}
    session.refresh(volume)
    assert volume.search_volume == 412 and volume.picked is True
    assert volume.provider == "aebrowse_google_ads"



def test_research_approval_keeps_auto_discover_agent_with_cross_platform_evidence(session):
    signals = [
        {"source": "etsy", "channel": "auto-discover", "title": "Embroidered hat",
         "url": "https://www.etsy.com/listing/123456789", "excerpt": "unverified"},
        {"source": "amazon", "channel": "auto-discover", "title": "Embroidered hat",
         "url": "https://www.amazon.com/dp/B012345678", "excerpt": "unverified"},
    ]
    assert import_signals(session, tenant_id="tenant-a",
                          user_id="scout-agent:agent-reach-id", signals=signals)["added"] == 2
    for row in list_signals(session, tenant_id="tenant-a"):
        review_signal(session, tenant_id="tenant-a", signal_id=row["id"],
                      user_id="reviewer-a", decision="approved", keyword="MADE TO WORSHIP")
    first = origins_for_keywords(session, tenant_id="tenant-a", keywords=["made to worship"])
    assert len(first["made to worship"]) == 1
    origin = first["made to worship"][0]
    assert origin["agent_key"] == "scout:agent-reach-id"
    assert origin["agent_name"] == "Agent-Reach Auto Discover"
    assert set(origin["platforms"]) == {"amazon", "etsy"}
    assert origin["evidence_count"] == 2
    assert {source["url"] for source in origin["sources"]} == {
        "https://www.etsy.com/listing/123456789",
        "https://www.amazon.com/dp/B012345678",
    }
    assert all(source["title"] == "Embroidered hat" for source in origin["sources"])
    assert apply_approved_to_stage0(session, tenant_id="tenant-a")["existing"] == 2
    assert origins_for_keywords(session, tenant_id="tenant-a",
                               keywords=["made to worship"]) == first


def test_auto_discover_import_promotes_clear_quotes_but_keeps_ambiguous_listings_pending(session):
    from app.modules.realistic_review_ugc.research_keyword_extraction import VERSION
    signals = [
        {"source": "etsy", "channel": "auto-discover",
         "title": "BAD DAY TO BE A BEER Embroidered Rope Hat",
         "url": "https://www.etsy.com/listing/123456789"},
        {"source": "amazon", "channel": "auto-discover",
         "title": "Custom Toddler Baseball Hat Boys Girls Personalized",
         "url": "https://www.amazon.com/dp/B012345678"},
        {"source": "etsy", "channel": "auto-discover",
         "title": "Created With a Purpose Embroidered Hat",
         "url": "https://www.etsy.com/listing/123456790"},
    ]
    result = import_signals(
        session, tenant_id="tenant-a", user_id="scout-agent:reach-id",
        signals=signals, auto_promote=True,
    )
    assert result == {"added": 3, "duplicates": 0, "auto_keywords": 2}
    assert {x["keyword"] for x in list_signals(session, tenant_id="tenant-a", status="approved")} == {
        "BAD DAY TO BE A BEER", "CREATED WITH A PURPOSE",
    }
    assert len(list_signals(session, tenant_id="tenant-a", status="pending")) == 1
    rows = session.query(RrugcKeywordVolumeModel).filter_by(tenant_id="tenant-a").all()
    assert len(rows) == 2
    assert all(row.provider == "pending" and row.request_count == 0
               and row.provider_raw_json["research_unverified"]
               and row.provider_raw_json["research_auto_extracted"] == VERSION
               for row in rows)
    origins = origins_for_keywords(session, tenant_id="tenant-a",
                                   keywords=[row.keyword_normalized for row in rows])
    assert all(agent[0]["agent_name"] == "Agent-Reach Auto Discover"
               for agent in origins.values())
    # The same batch may be resent on network retry, but no duplicate keyword
    # or bogus new source records should be inserted.
    again = import_signals(
        session, tenant_id="tenant-a", user_id="scout-agent:reach-id",
        signals=signals, auto_promote=True,
    )
    assert again == {"added": 0, "duplicates": 3, "auto_keywords": 0}
    assert session.query(RrugcKeywordVolumeModel).count() == 2


def test_backfill_requires_matching_dedicated_agent_and_never_changes_existing_approvals(session):
    dedicated = RrugcScoutAgentModel(
        id="reach-id", tenant_id="tenant-a", name="Agent-Reach Auto Discover",
        token_hash="a" * 64, active=True, created_by_user_id="operator",
    )
    session.add(dedicated)
    session.flush()
    valid = {"source": "pinterest", "channel": "auto-discover",
             "title": "Good Taste in Music Bad Taste in Men Trucker Hat",
             "url": "https://www.pinterest.com/pin/123456789"}
    import_signals(session, tenant_id="tenant-a", user_id="scout-agent:reach-id", signals=[valid])
    import_signals(session, tenant_id="tenant-b", user_id="scout-agent:reach-id",
                   signals=[valid])
    import_signals(session, tenant_id="tenant-a", user_id="scout-agent:other-id",
                   signals=[{"source":"etsy", "channel":"auto-discover",
                             "title":"DON'T QUIT Embroidered Hat",
                             "url":"https://www.etsy.com/listing/12345678"}])
    assert backfill_auto_discover_candidates(session, tenant_id="tenant-a", dry_run=True) == {
        "checked": 1, "auto_promoted": 1, "needs_review": 0
    }
    assert session.query(RrugcKeywordVolumeModel).count() == 0
    assert backfill_auto_discover_candidates(session, tenant_id="tenant-a") == {
        "checked": 1, "auto_promoted": 1, "needs_review": 0
    }
    assert backfill_auto_discover_candidates(session, tenant_id="tenant-a") == {
        "checked": 0, "auto_promoted": 0, "needs_review": 0
    }
    assert session.query(RrugcKeywordVolumeModel).count() == 1
    assert session.query(RrugcKeywordVolumeModel).one().keyword == "GOOD TASTE IN MUSIC BAD TASTE IN MEN"
    assert len(list_signals(session, tenant_id="tenant-a", status="pending")) == 1
    assert len(list_signals(session, tenant_id="tenant-b", status="pending")) == 1


def test_invalid_url_and_keyword_fail_closed(session):
    for url in ("http://news.google.com/a", "https://localhost/admin",
                "https://127.0.0.1/a", "https://foo.internal/a",
                "https://name:password@example.org/a"):
        with pytest.raises(ResearchValidationError):
            validate_url(url)
    for value in ("", "hello", "ONE TWO /;", "<script>"):
        with pytest.raises(ResearchValidationError):
            normalize_keyword(value)
    with pytest.raises(ResearchValidationError):
        import_signals(session,tenant_id="tenant-a",user_id="user-a",
                       signals=[_signal("http://localhost")])
    assert list_signals(session, tenant_id="tenant-a") == []


def test_export_has_no_auto_aebrowse_side_effect(session, monkeypatch):
    import httpx
    def denied(*_a, **_kw):
        raise AssertionError("Research import must not call an external provider")
    monkeypatch.setattr(httpx, "post", denied, raising=False)
    row = _signal()
    import_signals(session,tenant_id="test",user_id="test",signals=[row])
    selected = list_signals(session, tenant_id="test")[0]
    review_signal(session,tenant_id="test",user_id="operator",
                  signal_id=selected["id"],decision="approved",keyword="MORGAN WALLEN")
    assert stage0_export(session, tenant_id="test")["keywords"] == ["MORGAN WALLEN"]
