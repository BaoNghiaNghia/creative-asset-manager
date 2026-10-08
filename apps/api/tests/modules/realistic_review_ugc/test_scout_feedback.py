"""Manual Scout feedback remains tenant-safe and shared by all desktop agents."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel, RrugcScoutFeedbackModel,
)
from app.modules.realistic_review_ugc.scout_feedback import (
    blocked_targets, claim_priority, finish_priority,
    statuses_for_rows, update_feedback,
)


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
            assert finish_priority(session, "tenant-a", "agent-two", claim["id"], claim["lease_token"], True) is False
            assert finish_priority(session, "tenant-a", "agent-one", claim["id"], claim["lease_token"], True) is True
            assert claim_priority(session, "tenant-a", "agent-two") is None

            update_feedback(session, row, "suggested", "keyword", "user-a")
            session.commit()
            again = claim_priority(session, "tenant-a", "agent-two")
            assert again is not None and again["id"] == claim["id"]
    finally:
        engine.dispose()
