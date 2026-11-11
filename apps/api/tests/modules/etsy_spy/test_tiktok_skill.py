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



def test_generated_copy_repairs_only_formatting_not_product_facts():
    raw = {
        "product_title": copy()["title"],
        "product_description": copy()["description"],
        "product_highlights": copy()["highlights"] + ["Extra fact-free highlight.", "Drop this."],
        "searchTerms": ["mountain sweatshirt", "mountain sweatshirt",
                        "retro sunset pullover", "x" * 260, "outdoor gift"],
        "debug_reasoning": "Unexpected provider key is not a listing claim",
    }
    normalized = tiktok_skill.prepare_generated_copy(raw)
    assert set(normalized) == {"title", "description", "highlights", "search_terms"}
    assert normalized["highlights"] == (copy()["highlights"] +
                                        ["Extra fact-free highlight."])
    assert normalized["search_terms"] == [
        "mountain sweatshirt", "retro sunset pullover", "outdoor gift"]
    assert tiktok_skill.validate_copy(normalized)["published"] is False
    wrapped = tiktok_skill.prepare_generated_copy({"listing": {
        "title": copy()["title"], "description": copy()["description"],
        "highlights": copy()["highlights"],
    }})
    assert wrapped["search_terms"] == []
    assert tiktok_skill.validate_copy(wrapped)["metrics"]["search_terms_count"] == 0
    with pytest.raises(ValueError, match="unverified_us_origin_claim"):
        tiktok_skill.validate_copy(tiktok_skill.prepare_generated_copy({
            **copy(), "description": copy()["description"] + " Made in USA."
        }))
    with pytest.raises(tiktok_skill.GeneratedListingInvalid, match="missing_description"):
        tiktok_skill.prepare_generated_copy({"title": copy()["title"]})
    assert "RETRY" in tiktok_skill._retry_instruction(2)
    assert tiktok_skill._retry_instruction(1) == ""


def test_invalid_generated_copy_is_retryable_then_valid_draft_completes(db, monkeypatch):
    row = product()
    picked = EtsySpyPicked(tenant_id="boostgame", listing_key=row.listing_key,
                           picked_at=datetime.now(timezone.utc))
    db.add_all([row, picked])
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    job = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    assert job is not None
    job.status = "processing"
    job.claimed_by = "worker-cam"
    job.attempt_count = 1
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job.payload_json = {
        **job.payload_json,
        "_ai_model_start_slot": {
            "provider": "gemini", "credential_provider": "gemini",
            "model": "gemini-test-model", "worker_id": "worker-cam", "attempt_count": 1,
        },
    }
    db.commit()
    prompts = []

    class Provider:
        async def analyze_single(self, input):
            prompts.append(input.prompt)
            if len(prompts) == 1:
                return SimpleNamespace(metadata={**copy(), "description": "Too short."})
            return SimpleNamespace(metadata={**copy(), "search_terms": [
                "mountain sweatshirt", "mountain sweatshirt", "outdoor gift"]})

    deps = WorkerDependencies(session_factory=lambda: Session(db.get_bind()),
                              ai_provider_registry=SimpleNamespace(
                                  require=lambda key: Provider()))

    def context(attempt):
        return JobHandlerContext(
            job=ClaimedJob(
                id=job.id, tenant_id=job.tenant_id, job_type=job.job_type,
                entity_type=job.entity_type, entity_id=job.entity_id,
                payload=job.payload_json, attempt_count=attempt,
                lease_owner="worker-cam", provider_key="gemini"),
            dependencies=deps, shutdown_requested=Event(), cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger("test_tiktok_skill"), {}),
        )

    async def image(_url):
        return b"test-image-bytes", "image/jpeg"

    handler = tiktok_skill.EtsyTikTokListingHandler(fetch_image=image)
    first = handler(context(1))
    assert first.outcome == JobOutcome.RETRYABLE_FAILURE
    assert first.error_code == "tiktok_listing_invalid_output"
    assert "RETRY" not in prompts[0]
    db.expire_all()
    assert "result" not in db.get(ProcessingJobModel, job.id).payload_json

    job = db.get(ProcessingJobModel, job.id)
    job.attempt_count = 2
    job.payload_json = {
        **job.payload_json,
        "_ai_model_start_slot": {
            **job.payload_json["_ai_model_start_slot"],
            "attempt_count": 2,
        },
    }
    db.commit()
    second = handler(context(2))
    assert second.outcome == JobOutcome.COMPLETED
    assert "RETRY" in prompts[1]
    db.expire_all()
    result = db.get(ProcessingJobModel, job.id).payload_json["result"]
    assert result["published"] is False
    assert result["search_terms"] == ["mountain sweatshirt", "outdoor gift"]


def test_source_validation_error_remains_nonretryable():
    async def invalid_source(_context):
        raise ValueError("untrusted_image_url")

    handler = tiktok_skill.EtsyTikTokListingHandler()
    handler._run = invalid_source
    assert handler(object()).outcome == JobOutcome.NON_RETRYABLE_FAILURE


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
    monkeypatch.setattr(tiktok_skill, "auto_on_pick_enabled", lambda: False)
    assert tiktok_skill.queue_on_pick(
        db, "boostgame", row, picked.picked_at, automatic=True) is None
    first = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    assert first and first.status == "pending" and first.provider_key == "gemini"
    from app.modules.processing_policy.claim import RRUGC_GEMINI_ANALYZE_JOB_TYPES, MODEL_GATED_AI_JOB_TYPES
    assert tiktok_skill.JOB_TYPE in RRUGC_GEMINI_ANALYZE_JOB_TYPES
    assert tiktok_skill.JOB_TYPE in MODEL_GATED_AI_JOB_TYPES
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
    job.payload_json = {
        **job.payload_json,
        "_ai_model_start_slot": {
            "provider": "gemini", "credential_provider": "gemini",
            "model": "gemini-test-model", "worker_id": "worker-cam",
            "attempt_count": 1,
        },
    }
    job.attempt_count = 1
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db.commit()
    provider_calls = []
    class Provider:
        async def analyze_single(self, input):
            provider_calls.append(input.metadata_profile)
            assert input.tenant_id == "boostgame"
            assert input.preferred_model == "gemini-test-model"
            assert input.preferred_credential_provider == "gemini"
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

def test_manual_retry_is_bounded_and_preserves_failure_audit(db, monkeypatch):
    row = product()
    picked = EtsySpyPicked(tenant_id="boostgame", listing_key=row.listing_key,
                           picked_at=datetime.now(timezone.utc))
    db.add_all([row, picked])
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    original = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    assert original is not None
    original.status = "failed"
    original.attempt_count = 3
    original.last_error_code = "provider_failure"
    db.commit()
    once = tiktok_skill.retry_failed_draft(db, "boostgame", row, picked.picked_at)
    assert once.id == original.id and once.status == "pending"
    assert once.attempt_count == 0 and once.last_error_code is None
    assert once.payload_json["manual_retry_count"] == 1
    assert once.payload_json["prior_errors"][0]["code"] == "provider_failure"
    # Concurrent/manual duplicate calls while pending must not create work.
    again = tiktok_skill.retry_failed_draft(db, "boostgame", row, picked.picked_at)
    assert again.id == original.id
    assert again.payload_json["manual_retry_count"] == 1
    once.status = "failed"
    once.last_error_code = "model_unavailable"
    twice = tiktok_skill.retry_failed_draft(db, "boostgame", row, picked.picked_at)
    assert twice.status == "pending" and twice.payload_json["manual_retry_count"] == 2
    twice.status = "failed"
    exhausted = tiktok_skill.retry_failed_draft(db, "boostgame", row, picked.picked_at)
    assert exhausted.status == "failed"
    assert db.query(ProcessingJobModel).count() == 1
    exhausted.cancellation_requested = True
    assert tiktok_skill.retry_failed_draft(db, "boostgame", row, picked.picked_at).status == "failed"
