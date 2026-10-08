"""Manual Scout feedback remains tenant-safe and shared by all desktop agents."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel, RrugcScoutFeedbackModel,
)
from app.modules.realistic_review_ugc.scout_feedback import (
    blocked_targets, claim_priority, finish_priority, renew_priority,
    statuses_for_rows, update_feedback,
)


def test_scout_metrics_include_keyword_totals_and_review_runs():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc.router import list_scout_metrics
    from app.modules.realistic_review_ugc.model import (
        RrugcScoutMetricCycleModel, RrugcScoutRunModel,
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        RrugcKeywordVolumeModel.__table__,
        RrugcScoutFeedbackModel.__table__,
        RrugcScoutMetricCycleModel.__table__,
        RrugcScoutRunModel.__table__,
    ):
        table.create(engine)
    try:
        with Session(engine) as session:
            now = datetime.now(timezone.utc)
            session.add_all([
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-a", keyword="Cowboy Hat", keyword_normalized="cowboy hat",
                    created_at=now,
                ),
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-b", keyword="Houston Astros", keyword_normalized="houston astros",
                    created_at=now,
                ),
                RrugcScoutMetricCycleModel(
                    tenant_id="tenant-a", agent_id="agent-1", mode="keyword",
                    machine_label="PC-A", cycle_id="cycle-id-12345",
                    scanned_pins=12, found_quotes=7, new_keywords=5,
                    duplicate_pins=3, errors=0, created_at=now,
                ),
                RrugcScoutRunModel(
                    tenant_id="tenant-a", agent_id="agent-1", campaign_id="c1",
                    status="completed", query="trucker hat",
                    target_count=10, max_scroll_batches=2, auto_import=True,
                    submitted_count=9, created_count=5, existing_count=4, started_at=now,
                ),
            ])
            session.commit()
            result = list_scout_metrics(
                session=session, principal=SimpleNamespace(active_tenant_id="tenant-a"),
            )
            assert result["overview"]["total_keywords"] == 1
            assert result["overview"]["added_24h"] == 1
            assert result["items"][0]["machine_label"] == "PC-A"
            assert result["items"][0]["new_keywords"] == 5
            assert result["review_items"][0]["submitted"] == 9
            assert result["review_items"][0]["new_references"] == 5
            assert result["review_items"][0]["duplicates"] == 4
    finally:
        engine.dispose()


def test_keyword_and_pin_feedback_are_independently_reversible():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcScoutFeedbackModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            row = RrugcKeywordVolumeModel(
                tenant_id="tenant-a", keyword="Cowboy Hat",
                keyword_normalized="cowboy hat", source_pin_url="https://pinterest.com/pin/123/",
                source_image_url="https://i.pinimg.com/originals/aa.jpg",
            )
            session.add(row)
            session.commit()

            update_feedback(session, row, "blocked", "pin", "user-a")
            session.commit()
            assert blocked_targets(session, "tenant-a") == {
                "blocked_keywords": [], "blocked_pins": ["https://www.pinterest.com/pin/123/"],
            }
            assert blocked_targets(session, "tenant-b") == {
                "blocked_keywords": [], "blocked_pins": [],
            }
            statuses = statuses_for_rows(session, "tenant-a", [row])
            assert statuses[("pin", "https://www.pinterest.com/pin/123/")] == "blocked"

            update_feedback(session, row, "neutral", "pin", "user-a")
            update_feedback(session, row, "suggested", "keyword", "user-a")
            session.commit()
            assert blocked_targets(session, "tenant-a")["blocked_pins"] == []
            claim = claim_priority(session, "tenant-a", "agent-one")
            assert claim is not None and claim["type"] == "keyword"
            assert claim["keyword"] == "Cowboy Hat"
            assert claim_priority(session, "tenant-a", "agent-two") is None
            assert renew_priority(session, "tenant-a", "agent-two", claim["id"], claim["lease_token"]) is False
            assert renew_priority(session, "tenant-a", "agent-one", claim["id"], "bad-lease-token") is False
            assert renew_priority(session, "tenant-a", "agent-one", claim["id"], claim["lease_token"]) is True
            assert claim_priority(session, "tenant-a", "agent-two") is None
            assert finish_priority(session, "tenant-a", "agent-two", claim["id"], claim["lease_token"], True) is False
            assert finish_priority(session, "tenant-a", "agent-one", claim["id"], claim["lease_token"], True) is True
            assert claim_priority(session, "tenant-a", "agent-two") is None

            update_feedback(session, row, "suggested", "keyword", "user-a")
            session.commit()
            again = claim_priority(session, "tenant-a", "agent-two")
            assert again is not None and again["id"] == claim["id"]
    finally:
        engine.dispose()
