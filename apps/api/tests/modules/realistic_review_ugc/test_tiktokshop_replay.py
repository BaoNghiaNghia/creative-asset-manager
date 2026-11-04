"""Safeguard the bounded, idempotent TikTokShop V11 reference replay."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.analysis import (
    ANALYZER_VERSION, APPAREL_ANALYZER_VERSION,
)
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel, RrugcCandidateModel, RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.product_context import (
    TIKTOKSHOP_APPAREL_CONTEXT_VERSION,
)
from app.modules.realistic_review_ugc.tiktokshop_replay import (
    queue_legacy_tiktokshop_reference_replay,
    queue_tiktokshop_seed_prefilter_replay,
)
from app.modules.realistic_review_ugc.workspace import (
    RRUGC_SOURCE_ROOT_FOLDER_ID, TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
)

DATE = datetime(2026, 11, 2, tzinfo=timezone.utc)


def campaign_with_plan(session: Session, label: str, root: str, *, v5=True):
    campaign = RrugcCampaignModel(
        id="campaign-" + label, tenant_id="tenant-a", name=label,
        query="sweatshirt front candid", target_count=50,
        scout_token_hash="a" * 64, created_by_user_id="system",
        product_context_json={
            "visual_context": {
                "status": "ready",
                "version": TIKTOKSHOP_APPAREL_CONTEXT_VERSION if v5 else "v4",
            }
        },
    )
    plan = RrugcSourcePlanModel(
        id="plan-" + label, tenant_id="tenant-a", root_folder_id=root,
        source_file_id=label + "-photo", source_name=label + ".png",
        source_relative_path=label + ".png", source_mime_type="image/png",
        source_revision=label + "-hash", campaign_id=campaign.id,
        status="ready", visual_context_json={
            "status": "ready",
            "version": TIKTOKSHOP_APPAREL_CONTEXT_VERSION if v5 else "v4",
        }, created_by_user_id="system",
    )
    session.add_all([campaign, plan])
    return campaign


def candidate(session: Session, campaign: RrugcCampaignModel, name: str, *,
              status="drive_ready", analyzer=ANALYZER_VERSION, signal=None):
    row = RrugcCandidateModel(
        id=name, tenant_id="tenant-a", campaign_id=campaign.id,
        source_key=name, pin_url="https://pinterest.example/pin/" + name,
        image_url="https://images.example/" + name + ".jpg",
        analyzer_version=analyzer, analyzed_at=DATE,
        status=status, analysis_revision=1,
        remote_file_id="drive-" + name if status == "drive_ready" else None,
        imported_at=DATE if status == "drive_ready" else None,
        ai_signal_json=signal,
    )
    session.add(row)
    return row


def test_replays_old_imported_and_legacy_rejects_without_touching_other_roots_or_human_negatives():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        v5 = campaign_with_plan(db, "tik-v5", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
        v4 = campaign_with_plan(db, "tik-v4", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID, v5=False)
        gator = campaign_with_plan(db, "hat", RRUGC_SOURCE_ROOT_FOLDER_ID)
        good = candidate(db, v5, "good-import", signal={"reference_manual_label": "good"})
        rejected = candidate(db, v5, "old-hat-reject", status="rejected_head_ratio")
        candidate(db, v5, "manual-bad", signal={"reference_manual_label": "bad"})
        candidate(db, v5, "manual-ai", signal={"reference_manual_label": "ai"})
        candidate(db, v5, "duplicate", status="rejected_duplicate")
        candidate(db, v5, "already-new", analyzer=APPAREL_ANALYZER_VERSION)
        candidate(db, v5, "old-queued", status="analysis_queued")
        candidate(db, v4, "source-not-ready")
        candidate(db, gator, "hat-old")
        db.flush()

        assert queue_legacy_tiktokshop_reference_replay(db, "tenant-a") == 2
        db.commit()
        db.refresh(good)
        db.refresh(rejected)
        assert good.status == "analysis_queued"
        assert good.remote_file_id == "drive-good-import"
        assert good.imported_at.replace(tzinfo=timezone.utc) == DATE
        assert good.analysis_revision == 2
        assert good.ai_signal_json["reference_manual_label"] == "good"
        assert rejected.status == "analysis_queued"
        assert rejected.analysis_revision == 2
        assert db.scalar(select(func.count()).select_from(ProcessingJobModel)) == 2
        assert queue_legacy_tiktokshop_reference_replay(db, "tenant-a") == 0
        assert db.get(RrugcCandidateModel, "manual-bad").status == "drive_ready"
        assert db.get(RrugcCandidateModel, "manual-ai").status == "drive_ready"
        assert db.get(RrugcCandidateModel, "source-not-ready").status == "drive_ready"
        assert db.get(RrugcCandidateModel, "hat-old").status == "drive_ready"
    engine.dispose()




def test_tiktokshop_placement_queries_beat_legacy_quality_rank_without_windows_update():
    from app.modules.realistic_review_ugc.router import (
        _rank_scout_queries, prioritize_tiktokshop_placement_queries,
    )
    from app.modules.realistic_review_ugc.schema import ScoutQueryPerformance

    old = "vintage family group editorial photo"
    profile = {
        "visual_context": {
            "status": "ready",
            "version": TIKTOKSHOP_APPAREL_CONTEXT_VERSION,
        },
        "apparel_placements": [
            {"zone": "center_chest", "priority": "primary", "confidence": 0.97},
            {"zone": "left_sleeve", "priority": "secondary", "confidence": 0.94},
        ],
    }
    queries, scores = prioritize_tiktokshop_placement_queries(
        [old], [ScoutQueryPerformance(query=old, score=0.96)],
        {"product_type": "sweatshirt"}, profile,
    )
    ranked = _rank_scout_queries(queries, scores)
    assert "front upper body chest unobstructed" in ranked[0]
    assert "arm sleeves visible" in ranked[1]
    assert ranked[-1] == old
    assert max(score.score for score in scores) == 1.0
    hats, unchanged = prioritize_tiktokshop_placement_queries(
        [old], [ScoutQueryPerformance(query=old, score=0.96)],
        {"product_type": "cap"},
        {"visual_context": {"status": "ready", "version": "v4"}},
    )
    assert hats == [old] and unchanged[0].score == 0.96


def test_budget_limits_replay_without_overloading_existing_gemini_queue():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        v5 = campaign_with_plan(db, "tik-v5", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
        candidate(db, v5, "waiting-import")
        for i in range(5):
            row = candidate(db, v5, "existing-pending-" + str(i), status="analysis_queued")
            db.flush()
            db.add(ProcessingJobModel(
                tenant_id="tenant-a", job_type="rrugc_candidate_analyze",
                entity_type="rrugc_candidate", entity_id=row.id,
                idempotency_key="queued-" + str(i), status="pending",
                provider_key="gemini", provider_scope="ai",
            ))
        db.flush()
        assert queue_legacy_tiktokshop_reference_replay(
            db, "tenant-a", inflight_limit=5,
        ) == 0
        db.get(ProcessingJobModel, db.scalar(select(ProcessingJobModel.id).limit(1))).status = "completed"
        db.flush()
        assert queue_legacy_tiktokshop_reference_replay(
            db, "tenant-a", inflight_limit=5,
        ) == 1
        assert db.get(RrugcCandidateModel, "waiting-import").status == "analysis_queued"
        assert queue_legacy_tiktokshop_reference_replay(
            db, "tenant-a", inflight_limit=5,
        ) == 0
    engine.dispose()


def test_seed_prefilter_recovery_is_bounded_once_and_tiktokshop_only():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = campaign_with_plan(db, "first", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
        second = campaign_with_plan(db, "second", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
        hat = campaign_with_plan(db, "hat-seed", RRUGC_SOURCE_ROOT_FOLDER_ID)

        def seed_rejected(campaign, name, *, signal=None, reason="SEED_VISUAL_HIGH_CONFIDENCE_NEGATIVE"):
            row = candidate(db, campaign, name, status="rejected_context", signal=signal)
            row.reject_reason = reason
            row.analyzer_model = "siglip2-seed-gate-v1"
            return row

        for i in range(3):
            seed_rejected(first, "first-seed-" + str(i))
            seed_rejected(second, "second-seed-" + str(i))
        seed_rejected(first, "manual-bad-seed", signal={"reference_manual_label": "bad"})
        seed_rejected(first, "manual-ai-seed", signal={"reference_manual_label": "ai"})
        seed_rejected(first, "different-rejection", reason="PRODUCT_FIT_LOW")
        seed_rejected(hat, "gatorhat-seed")
        db.flush()

        assert queue_tiktokshop_seed_prefilter_replay(
            db, "tenant-a", batch_limit=2, inflight_limit=2,
        ) == 2
        db.flush()
        replayed = list(db.scalars(select(RrugcCandidateModel).where(
            RrugcCandidateModel.status == "analysis_queued",
        )))
        assert len(replayed) == 2
        assert len({row.campaign_id for row in replayed}) == 2
        assert all(row.analysis_revision == 2 for row in replayed)
        assert all(row.ai_signal_json["seed_replay_version"] for row in replayed)
        assert queue_tiktokshop_seed_prefilter_replay(
            db, "tenant-a", batch_limit=2, inflight_limit=2,
        ) == 0

        for job in db.scalars(select(ProcessingJobModel)):
            job.status = "completed"
        db.flush()
        assert queue_tiktokshop_seed_prefilter_replay(
            db, "tenant-a", batch_limit=2, inflight_limit=2,
        ) == 2
        assert db.get(RrugcCandidateModel, "manual-bad-seed").status == "rejected_context"
        assert db.get(RrugcCandidateModel, "manual-ai-seed").status == "rejected_context"
        assert db.get(RrugcCandidateModel, "different-rejection").status == "rejected_context"
        assert db.get(RrugcCandidateModel, "gatorhat-seed").status == "rejected_context"
        assert db.scalar(select(func.count()).select_from(ProcessingJobModel)) == 4
    engine.dispose()
