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


@pytest.fixture(autouse=True)
def prevent_external_ytrends_calls(monkeypatch):
    """Never hit a third-party MCP endpoint during CAM API tests."""
    async def no_data(_title, **_kwargs):
        return tiktok_skill.empty_evidence("vintage sunset mountain")
    monkeypatch.setattr(tiktok_skill, "research_etsy_keyword", no_data)
    # Legacy Gemini tests prove old in-flight jobs still work; new Codex tests
    # explicitly override the engine below.
    monkeypatch.setattr(tiktok_skill, "execution_engine", lambda: "gemini")


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
            "A vintage mountain sunset illustration creates a peaceful, "
            "outdoorsy mood on this sweatshirt design. The artwork shows "
            "layered ridges below a rounded sun with muted retro-style colors. "
            "It has a scenic composition that feels at home in relaxed outfits "
            "and appeals to people who enjoy mountains, nature and travel art.\n\n"
            "Design Details\n"
            "- Layered mountain ridges create depth across the front artwork.\n"
            "- A rounded sunset sits above the illustrated landscape.\n"
            "- Muted colors give the scene a vintage outdoor character.\n"
            "- The mountain and sun elements form a simple, balanced graphic.\n\n"
            "Size & Fit\n"
            "Check the size information for the particular sweatshirt you choose "
            "before ordering. If garment measurements are available, compare "
            "them to a sweatshirt you already own to help decide on your "
            "preferred silhouette and roominess without guessing a size.\n\n"
            "Perfect Gift For\n"
            "The mountain scene makes a thoughtful idea for hiking friends, "
            "nature lovers or someone who enjoys nostalgic landscape artwork. "
            "Its calm outdoor theme is also appropriate for casual gatherings, "
            "travel memories and relaxed everyday outfit inspiration.\n\n"
            "Care Instructions\n"
            "Follow the selected garment's actual care label and any "
            "item-specific instructions that accompany the product."
        ),
        "highlights": [
            "Retro mountain sunset artwork with layered ridges and scenic composition.",
            "Rounded sun and muted vintage-style colors make the front graphic distinctive.",
            "Sweatshirt design featuring an outdoorsy mountain illustration.",
            "An easy theme for casual gatherings, travel memories and nature-inspired outfits.",
            "A thoughtful gift idea for hikers, mountain travelers and lovers of landscape art.",
        ],
        "search_terms": [
            "mountain sunset sweatshirt", "retro mountain crewneck",
            "vintage landscape pullover", "outdoor hiking gift",
            "scenic mountain sweatshirt", "sunset landscape art",
            "nature lover gift", "retro scenic sweatshirt",
        ],
    }


def test_tiktok_copy_structural_validator_and_unverified_ytrends():
    result = tiktok_skill.validate_copy(copy())
    assert result["qa_status"] == "needs_fact_check"
    assert result["ytrends_status"] == "unavailable"
    assert result["published"] is False
    assert result["metrics"]["search_terms_count"] == 8
    assert result["metrics"]["description_words"] >= 130
    assert "Design Details\n-" in result["description"]
    assert "Care Instructions\n" in result["description"]
    assert len(result["highlights"]) == 5
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



def test_screenshot_style_editorial_outline_is_required():
    assert "Design Details" in tiktok_skill.PROMPT
    assert "Perfect Gift For" in tiktok_skill.PROMPT
    assert "Care Instructions" in tiktok_skill.PROMPT
    assert "exactly 5" in tiktok_skill.PROMPT
    assert "No U.S. Printing & Shipping section" in tiktok_skill.PROMPT
    valid = copy()
    assert tiktok_skill.validate_copy(valid)["metrics"]["description_words"] >= 130
    for bad in [
        {**valid, "description": valid["description"].replace("Design Details", "Design Notes")},
        {**valid, "description": valid["description"].replace(
            "\n- ", "\n  ") },
        {**valid, "description": valid["description"].replace(
            "Perfect Gift For", "Gift Suggestions")},
        {**valid, "highlights": valid["highlights"][:3]},
        {**valid, "search_terms": valid["search_terms"][:3]},
    ]:
        with pytest.raises(ValueError):
            tiktok_skill.validate_copy(bad)


@pytest.mark.parametrize("field,claim,error", [
    ("description", "Printed and shipped in the USA for domestic orders.",
     "unverified_origin_or_fulfillment_claim"),
    ("description", "No import customs fees on U.S. deliveries.",
     "unverified_origin_or_fulfillment_claim"),
    ("description", "U.S. Printing & Shipping",
     "unverified_origin_or_fulfillment_claim"),
    ("highlights", "Free shipping, no customs duties.",
     "unverified_origin_or_fulfillment_claim"),
    ("highlights", "Free shipping on every order.",
     "unverified_origin_or_fulfillment_claim"),
    ("description", "Machine wash cold with like colors and tumble dry low.",
     "unverified_material_or_care_claim"),
    ("description", "Our 100% cotton fleece blend feels cozy.",
     "unverified_material_or_care_claim"),
])
def test_screenshot_unverified_seller_claims_are_rejected(field, claim, error):
    sample = copy()
    if field == "highlights":
        sample["highlights"] = sample["highlights"][:-1] + [claim]
    else:
        sample[field] += "\n" + claim
    with pytest.raises(ValueError, match=error):
        tiktok_skill.validate_copy(sample)


def test_generated_copy_repairs_only_formatting_not_product_facts():
    raw = {
        "product_title": copy()["title"],
        "product_description": copy()["description"],
        "product_highlights": copy()["highlights"] + ["Extra fact-free highlight.", "Drop this."],
        "searchTerms": ["mountain sunset sweatshirt", "mountain sunset sweatshirt",
                        "retro mountain crewneck", "vintage landscape pullover",
                        "x" * 260, "outdoor hiking gift", "scenic mountain sweatshirt",
                        "sunset landscape art", "nature lover gift"],
        "debug_reasoning": "Unexpected provider key is not a listing claim",
    }
    normalized = tiktok_skill.prepare_generated_copy(raw)
    assert set(normalized) == {"title", "description", "highlights", "search_terms"}
    assert normalized["highlights"] == copy()["highlights"]
    assert normalized["search_terms"] == [
        "mountain sunset sweatshirt", "retro mountain crewneck",
        "vintage landscape pullover", "outdoor hiking gift",
        "scenic mountain sweatshirt", "sunset landscape art", "nature lover gift"]
    assert tiktok_skill.validate_copy(normalized)["published"] is False
    wrapped = tiktok_skill.prepare_generated_copy({"listing": {
        "title": copy()["title"], "description": copy()["description"],
        "highlights": copy()["highlights"],
    }})
    assert wrapped["search_terms"] == []
    with pytest.raises(ValueError):
        tiktok_skill.validate_copy(wrapped)
    with pytest.raises(ValueError, match="unverified_origin_or_fulfillment_claim"):
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
                "mountain sunset sweatshirt", "mountain sunset sweatshirt",
                "retro mountain crewneck", "vintage landscape pullover",
                "outdoor hiking gift", "scenic mountain sweatshirt",
                "sunset landscape art", "nature lover gift"]})

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
    assert result["search_terms"] == [
        "mountain sunset sweatshirt", "retro mountain crewneck",
        "vintage landscape pullover", "outdoor hiking gift",
        "scenic mountain sweatshirt", "sunset landscape art", "nature lover gift"]


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
    async def market_research(_title, **_kwargs):
        return {
            "status": "connected", "source": "trends.ytuong.ai",
            "marketplace": "etsy", "query": "vintage sunset mountain",
            "as_of_date": "2026-11-12", "median_data_age_days": 78,
            "total_listings": 36, "total_sellers": 31,
            "competition_level": "medium", "opportunity_score": 54.5,
            "dataset_note": "Indexed Etsy subset; not TikTok Shop search data.",
        }
    monkeypatch.setattr(tiktok_skill, "research_etsy_keyword", market_research)
    class Provider:
        async def analyze_single(self, input):
            assert "Etsy (not TikTok Shop)" in input.prompt
            assert "2026-11-12" in input.prompt
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
    assert stored.payload_json["result"]["ytrends_status"] == "connected"
    assert stored.payload_json["result"]["ytrends_evidence"]["marketplace"] == "etsy"
    assert stored.payload_json["result"]["ytrends_evidence"]["total_listings"] == 36
    assert "ytrends_etsy_snapshot_data_older_than_30_days" in stored.payload_json["result"]["warnings"]
    assert stored.payload_json["result"]["published"] is False
    # A cancelled/stale owner cannot overwrite the stored draft.
    db.delete(picked)
    db.commit()
    again = handler(context)
    assert again.outcome == JobOutcome.COMPLETED
    assert len(provider_calls) == 1

def test_regeneration_is_new_job_idempotent_and_bounded(db, monkeypatch):
    row = product()
    picked = EtsySpyPicked(tenant_id="boostgame", listing_key=row.listing_key,
                           picked_at=datetime.now(timezone.utc))
    db.add_all([row, picked])
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    original = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    assert original is not None
    old_draft = tiktok_skill.validate_copy(copy())
    original.status = "completed"
    original.payload_json = {**original.payload_json, "result": old_draft}
    db.commit()

    assert tiktok_skill.regenerate_completed_draft(
        db, "other", row, picked.picked_at) is None
    regenerated = tiktok_skill.regenerate_completed_draft(
        db, "boostgame", row, picked.picked_at)
    assert regenerated is not None and regenerated.id != original.id
    assert regenerated.status == "pending" and regenerated.provider_key == "gemini"
    assert regenerated.payload_json["regenerate_of_job_id"] == original.id
    assert regenerated.payload_json["regeneration_count"] == 1
    assert regenerated.payload_json["research_status"] == "ytrends_pending"
    assert tiktok_skill.project(regenerated)["can_regenerate"] is False
    db.commit()
    assert tiktok_skill.regenerate_completed_draft(
        db, "boostgame", row, picked.picked_at).id == regenerated.id
    assert db.query(ProcessingJobModel).count() == 2
    db.refresh(original)
    assert original.status == "completed"
    assert original.payload_json["result"] == old_draft

    # A failed regeneration must be retried instead of selecting the
    # first completed job's original idempotency key.
    regenerated.status = "failed"
    regenerated.last_error_code = "tiktok_listing_invalid_output"
    db.commit()
    retried = tiktok_skill.retry_failed_draft(db, "boostgame", row, picked.picked_at)
    assert retried.id == regenerated.id and retried.status == "pending"
    db.commit()

    regenerated = db.get(ProcessingJobModel, regenerated.id)
    regenerated.status = "completed"
    regenerated.payload_json = {**regenerated.payload_json, "result": old_draft}
    db.commit()
    next_job = tiktok_skill.regenerate_completed_draft(
        db, "boostgame", row, picked.picked_at)
    assert next_job is not None and next_job.id != regenerated.id
    assert next_job.payload_json["regeneration_count"] == 2
    assert next_job.payload_json["regenerate_of_job_id"] == regenerated.id
    db.commit()
    next_job.status = "completed"
    next_job.payload_json = {**next_job.payload_json, "result": old_draft}
    db.commit()
    assert tiktok_skill.regenerate_completed_draft(
        db, "boostgame", row, picked.picked_at) is None
    assert tiktok_skill.project(next_job)["can_regenerate"] is False
    assert db.query(ProcessingJobModel).count() == 3


def test_codex_cli_skill_runs_without_gemini_slot_or_provider(db, monkeypatch, tmp_path):
    row = product()
    picked = EtsySpyPicked(tenant_id="boostgame", listing_key=row.listing_key,
                           picked_at=datetime.now(timezone.utc))
    db.add_all([row, picked])
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    monkeypatch.setattr(tiktok_skill, "execution_engine", lambda: "codex")
    monkeypatch.setattr(tiktok_skill, "get_settings", lambda: SimpleNamespace(
        ETSY_TIKTOK_CODEX_HOME=str(tmp_path),
        ETSY_TIKTOK_CODEX_TIMEOUT_SECONDS=240,
        ETSY_TIKTOK_CODEX_MODEL="",
    ))
    job = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    assert job.provider_key == "codex"
    assert job.payload_json["engine"] == "codex"
    assert tiktok_skill.project(job)["generation_engine"] == "codex"
    job.status = "processing"
    job.claimed_by = "worker-cam"
    job.attempt_count = 1
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=6)
    db.commit()
    requests = []

    async def fake_codex_runner(**kwargs):
        requests.append(kwargs)
        assert kwargs["photo"] == b"test-image-bytes"
        assert kwargs["mime"] == "image/jpeg"
        assert "$tiktok-shop-listing-optimizer" in kwargs["prompt"]
        assert "UNTRUSTED_SOURCE_TITLE_JSON" in kwargs["prompt"]
        assert kwargs["codex_home"] == str(tmp_path)
        assert kwargs["model"] == ""
        assert kwargs["cancelled"]() is False
        return copy()

    async def photo(_url):
        return b"test-image-bytes", "image/jpeg"

    async def research(_title, **_kwargs):
        return tiktok_skill.empty_evidence("")

    deps = WorkerDependencies(session_factory=lambda: Session(db.get_bind()),
                              ai_provider_registry=None)
    claimed = ClaimedJob(
        id=job.id, tenant_id=job.tenant_id, job_type=job.job_type,
        entity_type=job.entity_type, entity_id=job.entity_id,
        payload=job.payload_json, attempt_count=1,
        lease_owner="worker-cam", provider_key="codex",
    )
    context = JobHandlerContext(
        job=claimed, dependencies=deps,
        shutdown_requested=Event(), cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("test_tiktok_codex"), {}),
    )
    handler = tiktok_skill.EtsyTikTokListingHandler(
        fetch_image=photo, research_ytrends=research, codex_runner=fake_codex_runner)
    out = handler(context)
    assert out.outcome == JobOutcome.COMPLETED
    assert len(requests) == 1
    db.expire_all()
    payload = db.get(ProcessingJobModel, job.id).payload_json
    assert payload["generation_provider"] == "codex_cli"
    assert payload["result"]["title"] == copy()["title"]
    assert payload["result"]["published"] is False


def test_explicit_upgrade_from_exhausted_gemini_to_codex(db, monkeypatch):
    row = product()
    picked = EtsySpyPicked(tenant_id="boostgame", listing_key=row.listing_key,
                           picked_at=datetime.now(timezone.utc))
    db.add_all([row, picked])
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    original = tiktok_skill.queue_on_pick(db, "boostgame", row, picked.picked_at)
    original.status = "completed"
    original.payload_json = {
        **original.payload_json, "skill_version": "tiktok-us-editorial-20261112-v2",
        "regeneration_count": 2, "result": tiktok_skill.validate_copy(copy()),
    }
    db.commit()
    assert tiktok_skill.project(original)["can_regenerate"] is True
    monkeypatch.setattr(tiktok_skill, "execution_engine", lambda: "codex")
    upgraded = tiktok_skill.regenerate_completed_draft(
        db, "boostgame", row, picked.picked_at)
    assert upgraded is not None
    assert upgraded.provider_key == "codex"
    assert upgraded.payload_json["engine"] == "codex"
    assert upgraded.payload_json["regeneration_count"] == 1
    db.commit()
    assert tiktok_skill.regenerate_completed_draft(
        db, "boostgame", row, picked.picked_at).id == upgraded.id


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
