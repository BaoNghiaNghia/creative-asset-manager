"""Bounded non-destructive TikTokShop v11 -> v12 candidate replay.

The existing source-plan auto-sync upgrades V4 image contexts to V5. Once the
source plan/campaign is ready, this queueing step re-evaluates legacy Pinterest
references for the real garment zones. It does not edit Drive assets, delete
candidates, reset human feedback, or trigger new image generation.
"""
from __future__ import annotations

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.analysis import APPAREL_ANALYZER_VERSION
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel, RrugcCandidateModel, RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.product_context import (
    TIKTOKSHOP_APPAREL_CONTEXT_VERSION,
)
from app.modules.realistic_review_ugc.service import RrugcService
from app.modules.realistic_review_ugc.workspace import (
    TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
)

TIKTOKSHOP_REPLAY_INFLIGHT_LIMIT = 24
TIKTOKSHOP_REPLAY_BATCH_LIMIT = 8
# Previously AI-rejected apparel may have been rejected because of old
# hat/head rules. Include those; duplicates and manually rejected references
# are never replayed without an explicit user action.
REPLAY_ELIGIBLE_STATUSES = frozenset({
    "drive_ready", "approved", "needs_review", "analysis_failed",
    "rejected_quality", "rejected_ai_risk", "rejected_context",
    "rejected_no_person", "rejected_head_ratio", "rejected_expression",
    "rejected_existing_headwear", "rejected_head_occlusion",
})


def queue_legacy_tiktokshop_reference_replay(
    session: Session,
    tenant_id: str,
    *,
    batch_limit: int = TIKTOKSHOP_REPLAY_BATCH_LIMIT,
    inflight_limit: int = TIKTOKSHOP_REPLAY_INFLIGHT_LIMIT,
) -> int:
    """Enqueue only stale candidate analyses belonging to ready V5 sources.

    The replay is self-draining and safe to call repeatedly (and concurrently).
    Each candidate gets at most one new attempt for this version, even when
    Gemini rejects it locally or terminally fails; existing job retries remain
    controlled by the standard processing queue.
    """
    source_rows = session.execute(
        select(
            RrugcSourcePlanModel.campaign_id,
            RrugcSourcePlanModel.visual_context_json,
        ).where(
            RrugcSourcePlanModel.tenant_id == tenant_id,
            RrugcSourcePlanModel.root_folder_id == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
            RrugcSourcePlanModel.status == "ready",
            RrugcSourcePlanModel.campaign_id.is_not(None),
        )
    )
    ready_ids: set[str] = set()
    for campaign_id, raw_context in source_rows:
        context = raw_context if isinstance(raw_context, dict) else {}
        if context.get("status") == "ready" and (
            context.get("version") == TIKTOKSHOP_APPAREL_CONTEXT_VERSION
        ):
            ready_ids.add(str(campaign_id))
    if not ready_ids:
        return 0

    # An old ready source can point at a shared campaign that was subsequently
    # refreshed from a different source. Confirm the campaign itself is V5.
    campaigns = list(session.scalars(
        select(RrugcCampaignModel).where(
            RrugcCampaignModel.tenant_id == tenant_id,
            RrugcCampaignModel.id.in_(sorted(ready_ids)),
            RrugcCampaignModel.status != "archived",
        )
    ))
    ready_ids = {
        row.id
        for row in campaigns
        if isinstance(row.product_context_json, dict)
        and isinstance(row.product_context_json.get("visual_context"), dict)
        and row.product_context_json["visual_context"].get("version")
        == TIKTOKSHOP_APPAREL_CONTEXT_VERSION
    }
    if not ready_ids:
        return 0

    all_tiktok_campaigns = select(RrugcSourcePlanModel.campaign_id).where(
        RrugcSourcePlanModel.tenant_id == tenant_id,
        RrugcSourcePlanModel.root_folder_id == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
        RrugcSourcePlanModel.campaign_id.is_not(None),
    ).distinct()
    pending = session.scalar(
        select(func.count(ProcessingJobModel.id)).join(
            RrugcCandidateModel,
            and_(
                RrugcCandidateModel.tenant_id == ProcessingJobModel.tenant_id,
                RrugcCandidateModel.id == ProcessingJobModel.entity_id,
            ),
        ).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == "rrugc_candidate_analyze",
            ProcessingJobModel.status.in_(("pending", "retry", "processing")),
            RrugcCandidateModel.campaign_id.in_(all_tiktok_campaigns),
        )
    ) or 0
    room = max(0, int(inflight_limit) - int(pending))
    batch = min(max(0, int(batch_limit)), room)
    if not batch:
        return 0

    rows = list(session.scalars(
        select(RrugcCandidateModel).where(
            RrugcCandidateModel.tenant_id == tenant_id,
            RrugcCandidateModel.campaign_id.in_(ready_ids),
            RrugcCandidateModel.status.in_(REPLAY_ELIGIBLE_STATUSES),
            RrugcCandidateModel.analyzed_at.is_not(None),
            RrugcCandidateModel.analyzer_version != APPAREL_ANALYZER_VERSION,
        ).order_by(
            case((RrugcCandidateModel.status == "drive_ready", 0), else_=1),
            RrugcCandidateModel.created_at.asc(),
            RrugcCandidateModel.id.asc(),
        ).limit(max(64, batch * 16)).with_for_update(skip_locked=True)
    ))
    service = RrugcService(session)
    count = 0
    for candidate in rows:
        if count >= batch:
            break
        signals = dict(candidate.ai_signal_json or {})
        if signals.get("reference_manual_label") in {"bad", "ai"}:
            continue
        if signals.get("apparel_replay_version") == APPAREL_ANALYZER_VERSION:
            continue
        # Keep all original image/import evidence intact. The handler promotes
        # a passing imported reference straight back to drive_ready without
        # uploading another copy of the same bytes.
        signals["apparel_replay_version"] = APPAREL_ANALYZER_VERSION
        signals["apparel_replay_from"] = candidate.analyzer_version
        candidate.ai_signal_json = signals
        service.enqueue_analysis(candidate, increment_revision=True)
        count += 1
    if count:
        session.flush()
    return count
