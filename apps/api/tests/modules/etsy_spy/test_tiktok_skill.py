"""Etsy Picked TikTok listing skill: idempotency, validation, tenant and QA."""
import logging
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, WorkerDependencies, JobOutcome
from app.modules.etsy_spy.model import EtsySpyListing, EtsySpyPicked
from app.modules.etsy_spy import tiktok_skill
from app.modules.processing.model import ProcessingJobModel


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        EtsySpyListing.__table__, EtsySpyPicked.__table__, ProcessingJobModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def product():
    return EtsySpyListing(
        tenant_id="boostgame", agent_id="a"*36, source_session_id="s",
        keyword="vintage nature sweatshirt", source_kind="search",
        is_current=True, listing_key="1234567890", title="Vintage Sunset Mountain Sweatshirt",
        metadata_json={
            "fullImageUrl": "https://i.etsystatic.com/12345/il_fullxfull.abc.jpg",
        },
    )


def copy():
    return {
        "title": "Vintage Sunset Mountain Sweatshirt for Outdoor Adventure Fans",
        "description": (
            "A retro mountain sunset illustration brings a warm outdoor mood "
            "to this sweatshirt design. The artwork features layered hills, "
            "a setting sun, and vintage-inspired colors. Wear the design on "
            "relaxed days or share it with someone who enjoys mountain trips. "
            "For fit, please compare the measurements provided for the selected "
            "garment before ordering. Follow the care label for proper washing."
        ),
        "highlights": [
            "Retro mountain sunset design with layered hills.",
            "A thoughtful theme for outdoor and hiking enthusiasts.",
            "Sweatshirt silhouette with vintage-inspired illustration.",
            "Great for casual outfits and outdoorsy gift occasions.",
        ],
        "search_terms": ["mountain sweatshirt", "retro sunset pullover", "outdoor gift"],
    }


def test_tiktok_copy_structural_validator_and_unverified_ytrends():
    result = tiktok_skill.validate_copy(copy())
    assert result["qa_status"] == "needs_fact_check"
    assert result["ytrends_status"] == "unavailable"
    assert result["published"] is False
    assert result["metrics"]["search_terms_count"] == 3
    assert result["metrics"]["title_characters"] == len(copy()["title"])
    assert not result["metrics"].get("search_volume")
    for mutation in [
        {"title": "Too short"},
        {"description": "Few words"},
        {"highlights": ["X" * 800, "Y" * 800, "Z"]},
        {"search_terms": ["dupe", "dupe"]},
        {"search_terms": ["tag" + str(i) + "x" * 17 for i in range(12)]},
        {"description": copy()["description"] + " Made in USA"},
    ]:
        with pytest.raises((ValueError, Exception)):
            tiktok_skill.validate_copy({**copy(), **mutation})


def test_queue_is_feature_gated_idempotent_and_does_not_share_tenant(db, monkeypatch):
    row = product()
    picked = EtsySpyPicked(tenant_id="boostgame", listing_key=row.listing_key,
                           picked_at=datetime.now(timezone.utc))
    db.add_all([row, picked])
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: False)
    assert tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at) is None
    assert db.query(ProcessingJobModel).count() == 0
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    first = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    assert first and first.status == "pending" and first.provider_key == "gemini"
    again = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    assert again.id == first.id
    assert db.query(ProcessingJobModel).count() == 1
    assert tiktok_skill.latest_job(db, "other", row.listing_key) is None
    tiktok_skill.cancel_for_unpick(db, "other", row.listing_key)
    assert first.cancellation_requested is False
    tiktok_skill.cancel_for_unpick(db, "boostgame", row.listing_key)
    assert first.cancellation_requested is True
    assert tiktok_skill.project(first)["status"] == "cancelled"


def test_handler_generates_draft_and_persists_only_owned_active_job(db, monkeypatch):
    row = product()
    picked = EtsySpyPicked(tenant_id="boostgame", listing_key=row.listing_key,
                           picked_at=datetime.now(timezone.utc))
    db.add_all([row, picked])
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    job = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    assert job is not None
    job.status, job.claimed_by = "processing", "worker-cam"
    job.attempt_count = 1
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db.commit()
    provider_calls = []
    class Provider:
        async def analyze_single(self, input):
            provider_calls.append(input.metadata_profile)
            assert input.tenant_id == "boostgame"
            assert input.image_bytes == b"test-image-bytes"
            return SimpleNamespace(metadata=copy())
    provider_registry = SimpleNamespace(require=lambda key: Provider())
    deps = WorkerDependencies(session_factory=lambda: Session(db.get_bind()),
                              ai_provider_registry=provider_registry)
    claimed = ClaimedJob(
        id=job.id, tenant_id=job.tenant_id, job_type=job.job_type,
        entity_type=job.entity_type, entity_id=job.entity_id,
        payload=job.payload_json, attempt_count=1,
        lease_owner="worker-cam", provider_key="gemini",
    )
    context = JobHandlerContext(
        job=claimed, dependencies=deps,
        shutdown_requested=Event(), cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("test_tiktok_skill"), {}),
    )
    async def image(_url): return b"test-image-bytes", "image/jpeg"
    handler = tiktok_skill.EtsyTikTokListingHandler(fetch_image=image)
    out = handler(context)
    assert out.outcome == JobOutcome.COMPLETED
    assert provider_calls == ["etsy_tiktok_listing_draft"]
    db.expire_all()
    stored = db.get(ProcessingJobModel, job.id)
    assert stored.payload_json["result"]["title"] == copy()["title"]
    assert stored.payload_json["result"]["ytrends_status"] == "unavailable"
    assert stored.payload_json["result"]["published"] is False
    # A cancelled/stale owner cannot overwrite the stored draft.
    db.delete(picked)
    db.commit()
    again = handler(context)
    assert again.outcome == JobOutcome.COMPLETED
    assert len(provider_calls) == 1