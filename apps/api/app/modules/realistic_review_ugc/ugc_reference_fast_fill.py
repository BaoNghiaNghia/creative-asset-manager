"""Fast, provenance-safe Products Slot 6 warm start from already verified UGC.

No synthetic reference, unreviewed Pinterest pin, quota-consuming reanalysis,
cross-tenant source, or TikTokShop source may be imported. Reused images are
local per-design duplicate rows with independent ✓/✕/AI feedback.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import (
    RrugcCandidateModel, RrugcCampaignModel, RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.ugc_scene_selection import (
    allowed_scout_image_url, eligible_ugc_references,
)
from app.modules.realistic_review_ugc.ugc_verified_reuse import (
    load_products_ugc_references,
)
from app.modules.realistic_review_ugc.workspace import TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID

# Restrict to results that already passed the existing Review approval pipeline.
_VERIFIED = ("approved", "drive_ready")
_POOL_LIMIT = 650


def warm_start_products_ugc(
    session: Session, *, plan: RrugcSourcePlanModel,
    campaign: RrugcCampaignModel, target: int = 20,
) -> dict[str, int]:
    """Fill a specific ready Product plan with independent verified lifestyle refs.

    The caller must own the transaction and a lock on the source plan.
    Repeated calls are idempotent; rejected user choices remain rejected.
    """
    if (plan.root_folder_id != PRODUCTS_STAGE1_SELECTED_ROOT
            or plan.status != "ready"
            or plan.tenant_id != campaign.tenant_id
            or plan.campaign_id != campaign.id
            or campaign.status == "archived"):
        return {"eligible": 0, "created": 0}
    # If multiple source plans share a campaign they must refer to exactly the
    # same embroidery; unlike Gallery, do not silently cross-design warm start.
    related = list(session.scalars(select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.tenant_id == plan.tenant_id,
        RrugcSourcePlanModel.campaign_id == campaign.id,
        RrugcSourcePlanModel.status != "missing",
    )))
    if len(related) > 1 and (
        not plan.embroidery_signature or
        any(p.embroidery_signature != plan.embroidery_signature
            for p in related if p.id != plan.id)
    ):
        return {"eligible": 0, "created": 0}

    desired = max(0, min(20, int(target)))
    current = eligible_ugc_references(load_products_ugc_references(
        session, tenant_id=plan.tenant_id, campaign_id=campaign.id,
    ), limit=20)
    if len(current) >= desired:
        return {"eligible": len(current), "created": 0}
    # Existing per-design pin votes, including negatives, are immutable.
    rows = list(session.scalars(select(RrugcCandidateModel).where(
        RrugcCandidateModel.tenant_id == plan.tenant_id,
        RrugcCandidateModel.campaign_id == campaign.id,
    )))
    local_keys = {r.source_key for r in rows}
    local_pins = {r.pin_url for r in rows}
    existing_hashes = {
        c.content_hash or c.image_url.strip().casefold() for c in current
    }
    non_tiktok = ~exists(select(RrugcSourcePlanModel.id).where(
        RrugcSourcePlanModel.tenant_id == plan.tenant_id,
        RrugcSourcePlanModel.campaign_id == RrugcCandidateModel.campaign_id,
        RrugcSourcePlanModel.root_folder_id == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
    ))
    # Force visible wearer and acceptable AI score: a random hat/product photo
    # is not a verified candid reference.
    pool = list(session.scalars(select(RrugcCandidateModel).where(
        RrugcCandidateModel.tenant_id == plan.tenant_id,
        RrugcCandidateModel.campaign_id != campaign.id,
        RrugcCandidateModel.status.in_(_VERIFIED),
        RrugcCandidateModel.head_visible.is_(True),
        RrugcCandidateModel.people_count >= 1,
        RrugcCandidateModel.mobile_ugc_score >= 0.55,
        RrugcCandidateModel.final_score >= 0.62,
        non_tiktok,
    ).order_by(RrugcCandidateModel.final_score.desc().nullslast(),
               RrugcCandidateModel.created_at.desc()).limit(_POOL_LIMIT)))
    # Apply existing URL/feedback/quality gates and stable, design-specific
    # sampling. Do not take all 20 from a single original campaign.
    eligible = [
        row for row in eligible_ugc_references(pool, limit=_POOL_LIMIT)
        if row.ai_manual_label != "ai"
        and allowed_scout_image_url(row.image_url)
        and row.source_key not in local_keys
        and row.pin_url not in local_pins
        and (row.content_hash or row.image_url.strip().casefold()) not in existing_hashes
    ]
    eligible.sort(key=lambda row: (
        sha256(f"{plan.id}:{plan.source_revision}:{row.id}".encode()).hexdigest(),
        row.id,
    ))
    selected = []
    campaigns = Counter()
    seen = set(existing_hashes)
    seen_source_keys = set(local_keys)
    seen_pins = set(local_pins)
    for quota in (3, 20):
        for source in eligible:
            if len(selected) + len(current) >= desired:
                break
            identity = source.content_hash or source.image_url.strip().casefold()
            if (identity in seen or source.source_key in seen_source_keys
                    or source.pin_url in seen_pins
                    or campaigns[source.campaign_id] >= quota):
                continue
            selected.append(source)
            seen.add(identity)
            seen_source_keys.add(source.source_key)
            seen_pins.add(source.pin_url)
            campaigns[source.campaign_id] += 1
        if len(selected) + len(current) >= desired:
            break
    created = 0
    for source in selected:
        flags = {
            "duplicate_exact_candidate_id": source.id,
            "duplicate_exact_campaign_id": source.campaign_id,
            "duplicate_exact_source_key": source.source_key,
            "ugc_fast_fill": "verified_same_tenant_v1",
        }
        session.add(RrugcCandidateModel(
            tenant_id=plan.tenant_id,
            campaign_id=campaign.id,
            source_key=source.source_key,
            pin_url=source.pin_url,
            image_url=source.image_url,
            alt_text=source.alt_text,
            ai_signal_json=flags,
            status="rejected_duplicate",
            analysis_revision=1,
            reject_reason="EXACT_SOURCE_DUPLICATE",
        ))
        created += 1
    if created:
        session.flush()
    count = len(eligible_ugc_references(load_products_ugc_references(
        session, tenant_id=plan.tenant_id, campaign_id=campaign.id,
    ), limit=20))
    lease_expiry = campaign.scan_lease_expires_at
    if lease_expiry is not None and lease_expiry.tzinfo is None:
        lease_expiry = lease_expiry.replace(tzinfo=timezone.utc)
    if count >= desired and (
        not campaign.scan_lease_run_id or
        lease_expiry is None or lease_expiry <= datetime.now(timezone.utc)
    ):
        campaign.status = "completed"
        campaign.scout_status = "ready"
        campaign.scan_next_at = None
        campaign.completed_at = datetime.now(timezone.utc)
    elif count < desired and campaign.status == "completed":
        campaign.status = "running"
        campaign.completed_at = None
        if campaign.auto_scout and campaign.scan_next_at is None:
            campaign.scan_next_at = datetime.now(timezone.utc)
    return {"eligible": count, "created": created}
