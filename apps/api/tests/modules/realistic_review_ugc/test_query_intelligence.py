"""Search Intelligence v2: cross-agent leasing, discovery feedback and scoring."""
from __future__ import annotations

from datetime import timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel, RrugcScoutFeedbackModel, RrugcScoutQueryModel,
)
from app.modules.realistic_review_ugc.query_intelligence import (
    claim_query, finish_query, intelligence_summary, normalized,
    seed_pool, cap_query, style_query, utcnow,
)


def create_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcScoutFeedbackModel.__table__.create(engine)
    RrugcScoutQueryModel.__table__.create(engine)
    return engine


def test_query_expansion_remains_product_focused_and_not_one_seed():
    assert cap_query("Houston Astros") == "Houston Astros trucker hat"
    assert cap_query("Retro Saying", 1) == "Retro Saying embroidered cap"
    assert cap_query("funny retro hat") == "funny retro hat"
    assert "hat" in style_query("funny hotdog")
    assert "cap" in style_query("vintage western") or "hat" in style_query("vintage western")


def test_feedback_creates_multiple_lanes_and_blocked_keywords_are_not_explored():
    engine = create_db()
    try:
        with Session(engine) as s:
            s.add_all([
                RrugcScoutFeedbackModel(
                    tenant_id="tenant-a", target_type="keyword", target_key="hotdog comedy",
                    display_value="Hotdog Comedy", keyword="Hotdog Comedy", status="suggested",
                    updated_by_user_id="u",
                ),
                RrugcScoutFeedbackModel(
                    tenant_id="tenant-a", target_type="keyword", target_key="brand blocked",
                    display_value="Brand Blocked", keyword="Brand Blocked", status="blocked",
                    updated_by_user_id="u",
                ),
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-a", keyword="Western Joke",
                    keyword_normalized="western joke", favorite=True, trademark_status="safe",
                ),
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-b", keyword="Other Tenant Secret",
                    keyword_normalized="other tenant secret", favorite=True,
                ),
            ])
            s.commit()
            seed_pool(s, "tenant-a")
            pool = s.scalars(select(RrugcScoutQueryModel).where(
                RrugcScoutQueryModel.tenant_id == "tenant-a",
            )).all()
            assert len(pool) >= 25
            assert {"suggested", "style", "product", "explore"}.issubset({r.lane for r in pool})
            assert any("hotdog" in r.query_normalized and "hat" in r.query_normalized for r in pool)
            assert all("other tenant secret" not in r.query_normalized for r in pool)
            assert all("brand blocked" not in r.query_normalized for r in pool)
    finally:
        engine.dispose()


def test_lease_exclusive_between_agents_and_completion_tracks_performance():
    engine = create_db()
    try:
        with Session(engine) as s:
            first = claim_query(s, "tenant-a", "agent-1")
            assert first is not None
            second = claim_query(s, "tenant-a", "agent-2")
            assert second is not None and first["id"] != second["id"]
            assert first["query"] != second["query"]
            assert not finish_query(s, "tenant-a", "agent-2", first["id"], first["lease_token"],
                                    success=True, scanned_pins=10, found_quotes=3,
                                    new_keywords=2, duplicate_pins=1)
            assert finish_query(s, "tenant-a", "agent-1", first["id"], first["lease_token"],
                                success=True, scanned_pins=10, found_quotes=3,
                                new_keywords=2, duplicate_pins=1)
            assert not finish_query(s, "tenant-a", "agent-1", first["id"], first["lease_token"],
                                    success=True, scanned_pins=99, found_quotes=3,
                                    new_keywords=9, duplicate_pins=1)
            old = s.get(RrugcScoutQueryModel, first["id"])
            assert old.completed_cycles == 1 and old.new_keywords == 2
            assert old.scanned_pins == 10 and old.duplicate_pins == 1
            state = intelligence_summary(s, "tenant-a")
            assert state["cycles_completed"] == 1
            assert state["active_leases"] == 1
            assert state["new_keywords"] == 2
    finally:
        engine.dispose()


def test_duplicate_no_progress_queries_cool_down_and_lease_recovery():
    engine = create_db()
    try:
        with Session(engine) as s:
            task = claim_query(s, "tenant-a", "agent-1")
            assert task
            row = s.get(RrugcScoutQueryModel, task["id"])
            row.lease_expires_at = utcnow() - timedelta(minutes=1)
            # Occupy all other candidates to specifically exercise expiry
            # reclaim instead of ordinary fair-share selection.
            for candidate in s.scalars(select(RrugcScoutQueryModel)).all():
                if candidate.id != task["id"]:
                    candidate.lease_token = "other-lease"
                    candidate.claimed_by_agent_id = "agent-existing"
                    candidate.lease_expires_at = utcnow() + timedelta(hours=1)
            s.commit()
            claimed_elsewhere = claim_query(s, "tenant-a", "agent-2")
            assert claimed_elsewhere and claimed_elsewhere["id"] == task["id"]
            assert not finish_query(s, "tenant-a", "agent-1", task["id"], task["lease_token"],
                                    success=False, scanned_pins=0, found_quotes=0,
                                    new_keywords=0, duplicate_pins=0)
            assert finish_query(s, "tenant-a", "agent-2", task["id"], claimed_elsewhere["lease_token"],
                                success=True, scanned_pins=0, found_quotes=0,
                                new_keywords=0, duplicate_pins=0)
            fresh = s.get(RrugcScoutQueryModel, task["id"])
            assert fresh.empty_cycles == 1
            # All other candidates are leased, and the completed no-progress
            # query is cooling down, so the scheduler must not repeat it.
            next_task = claim_query(s, "tenant-a", "agent-3")
            assert next_task is None
    finally:
        engine.dispose()

def test_query_renewal_preserves_exclusive_lease():
    from app.modules.realistic_review_ugc.query_intelligence import renew_query

    engine = create_db()
    try:
        with Session(engine) as session:
            task = claim_query(session, "tenant-a", "agent-1")
            assert task is not None
            assert not renew_query(session, "tenant-a", "agent-2", task["id"], task["lease_token"])
            assert renew_query(session, "tenant-a", "agent-1", task["id"], task["lease_token"])
            row = session.get(RrugcScoutQueryModel, task["id"])
            assert row.claimed_by_agent_id == "agent-1"
            assert not finish_query(
                session, "tenant-a", "agent-2", task["id"], task["lease_token"],
                success=False, scanned_pins=0, found_quotes=0,
                new_keywords=0, duplicate_pins=0,
            )
            assert finish_query(
                session, "tenant-a", "agent-1", task["id"], task["lease_token"],
                success=True, scanned_pins=100, found_quotes=20,
                new_keywords=10, duplicate_pins=30,
            )
            assert row.lease_token is None
    finally:
        engine.dispose()
