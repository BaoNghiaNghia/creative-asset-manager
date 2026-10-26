"""Read-only reuse of verified lifestyle sources for Products Slot 6.

Pinterest repeats the same popular images between design-specific campaigns.
The regular Scout pipeline correctly rejects cross-campaign duplicates to avoid
repeat AI processing. For Products UGC references (not review candidates),
we can safely reuse an *already approved* source from the same tenant, without
changing duplicate status, rescanning, re-uploading or bypassing human feedback.

The returned per-design proxy keeps the local duplicate candidate ID so
votes (good/bad/AI) remain scoped to the design's own campaign.
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcCandidateModel
from app.modules.realistic_review_ugc.ugc_scene_selection import (
    APPROVED_STATUSES, allowed_scout_image_url, eligible_ugc_references,
)

_VERIFIED_SOURCE_STATUSES = frozenset({"approved", "drive_ready"})


def load_products_ugc_references(
    session: Session, *, tenant_id: str, campaign_id: str, limit: int = 150,
) -> list:
    """Return real approved candidates plus safe per-campaign reused references.

    No database writes. Same-tenant and per-design checks must both pass.
    Results are sorted by reference quality and shared by gallery and worker.
    """
    current = list(session.scalars(
        select(RrugcCandidateModel).where(
            RrugcCandidateModel.tenant_id == tenant_id,
            RrugcCandidateModel.campaign_id == campaign_id,
            RrugcCandidateModel.status.in_(APPROVED_STATUSES),
        ).order_by(RrugcCandidateModel.final_score.desc().nullslast(),
                   RrugcCandidateModel.created_at.desc()).limit(limit)
    ))
    exact_duplicates = list(session.scalars(
        select(RrugcCandidateModel).where(
            RrugcCandidateModel.tenant_id == tenant_id,
            RrugcCandidateModel.campaign_id == campaign_id,
            RrugcCandidateModel.status == "rejected_duplicate",
            RrugcCandidateModel.reject_reason == "EXACT_SOURCE_DUPLICATE",
        ).order_by(RrugcCandidateModel.created_at.desc()).limit(300)
    ))
    ids = set()
    for duplicate in exact_duplicates:
        flags = duplicate.ai_signal_json if isinstance(duplicate.ai_signal_json, dict) else {}
        source_id = flags.get("duplicate_exact_candidate_id")
        if isinstance(source_id, str) and source_id:
            ids.add(source_id)
    if not ids:
        return current

    originals = {
        row.id: row for row in session.scalars(
            select(RrugcCandidateModel).where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.id.in_(ids),
                RrugcCandidateModel.status.in_(_VERIFIED_SOURCE_STATUSES),
            )
        )
    }
    reused = []
    for duplicate in exact_duplicates:
        flags = duplicate.ai_signal_json if isinstance(duplicate.ai_signal_json, dict) else {}
        if flags.get("reference_manual_label") in ("bad", "ai"):
            continue
        source = originals.get(flags.get("duplicate_exact_candidate_id"))
        if source is None or source.campaign_id == campaign_id:
            continue
        if source.ai_manual_label == "ai" or not eligible_ugc_references([source]):
            continue
        if not allowed_scout_image_url(source.image_url):
            continue
        reused.append(SimpleNamespace(
            id=duplicate.id,
            campaign_id=campaign_id,
            image_url=source.image_url,
            pin_url=source.pin_url,
            status=source.status,
            final_score=source.final_score,
            head_visible=source.head_visible,
            people_count=source.people_count,
            mobile_ugc_score=source.mobile_ugc_score,
            content_hash=source.content_hash,
            diversity_signature=source.diversity_signature,
            ai_signal_json=flags,
            reused_verified_source=True,
        ))
    # Prefer strongest verified references. The same 20 become gallery and
    # deterministic generation inputs; sorting has a stable ID tie-breaker.
    combined = current + reused
    combined.sort(key=lambda item: (
        -(item.final_score if item.final_score is not None else -1),
        item.id,
    ))
    return combined[:limit]
