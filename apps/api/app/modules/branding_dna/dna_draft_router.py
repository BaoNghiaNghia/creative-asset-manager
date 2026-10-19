"""Read-only, evidence-backed Brand DNA worksheet.

This is NOT a model-generated Brand DNA, an approval action or a permission
grant. It exposes only independently verified listing metadata as product
facts, and approved RAG as labeled contextual references. No media is used.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.dna_readiness_router import preview_dna_inputs
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-draft"])
CONFIGURE = require_permission("ai_provider.configure")
MAX_EXAMPLES = 12
MAX_CATEGORIES = 12


@router.get("/shops/{shop_id}/dna-draft/preview")
def preview_dna_draft(
    shop_id: str, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    """Draft a strictly factual, bounded outline; never persist or dispatch."""
    readiness = preview_dna_inputs(
        shop_id, response, session=session, principal=principal,
    )
    tenant = principal.active_tenant_id
    scoped = (
        BrandListingSnapshotModel.tenant_id == tenant,
        BrandListingSnapshotModel.shop_id == shop_id,
        BrandListingSnapshotModel.evidence_state == "verified",
    )
    total_verified = session.scalar(
        select(func.count()).select_from(BrandListingSnapshotModel).where(*scoped)
    ) or 0
    examples = session.scalars(
        select(BrandListingSnapshotModel).where(*scoped)
        .order_by(
            BrandListingSnapshotModel.captured_at.desc(),
            BrandListingSnapshotModel.id.desc(),
        )
        .limit(MAX_EXAMPLES)
    ).all()
    grouped = session.execute(
        select(BrandListingSnapshotModel.category, func.count())
        .where(*scoped)
        .group_by(BrandListingSnapshotModel.category)
        .order_by(func.count().desc(), BrandListingSnapshotModel.category)
        .limit(MAX_CATEGORIES + 1)
    ).all()
    # Listing facts and RAG text are explicitly segregated: a shop note is
    # not independently verified product, bestseller, audience or sales proof.
    blockers = list(readiness["blockers"])
    if not total_verified and "verified_product_evidence_missing" not in blockers:
        blockers.append("verified_product_evidence_missing")
    snapshot_truncated = total_verified > MAX_EXAMPLES
    categories_truncated = len(grouped) > MAX_CATEGORIES
    if snapshot_truncated:
        blockers.append("draft_example_sample_truncated")
    if categories_truncated:
        blockers.append("draft_category_list_truncated")
    references = [
        {
            "document_id": item["document_id"],
            "filename": item["filename"],
            "category": item["category"],
            "citation": item["citation"],
            "trust": "human_approved_context_not_independent_product_proof",
        }
        for item in readiness["knowledge_sources"]
    ]
    response.headers["Cache-Control"] = "no-store"
    return {
        "shop": readiness["shop"],
        "input_fingerprint": readiness["input_fingerprint"],
        "status": "evidence_outline" if total_verified and not blockers
                  else "blocked",
        "preview_only": True,
        "persisted": False,
        "model_invoked": False,
        "dna_generation_enabled": False,
        "automatic_dna_mutation_enabled": False,
        "visual_analysis_enabled": False,
        "image_rights_verified": False,
        "best_seller_verified": False,
        "brand_pick_verified": False,
        "verified_listing_count": total_verified,
        "examples_truncated": snapshot_truncated,
        "categories_truncated": categories_truncated,
        "observed_categories": [
            {"category": name, "listing_count": count}
            for name, count in grouped[:MAX_CATEGORIES]
        ],
        "verified_product_examples": [
            {
                "snapshot_id": row.id,
                "source_revision": row.source_revision,
                "title": row.title,
                "category": row.category,
                "citation": f"listing:{row.id}",
                "source_state": "verified_listing_metadata_only",
            }
            for row in examples
        ],
        "context_references": references,
        "unresolved_sections": [
            "visual_style_and_image_usage_rights",
            "brand_voice_requires_human_synthesis",
            "target_audience",
            "brand_best_seller_claims",
            "brand_pick_claims",
        ],
        "blockers": blockers,
        "notices": readiness["notices"],
        "trust_rule": (
            "Only independently verified listing metadata is reported as "
            "product facts. Approved RAG is contextual, not product proof; "
            "no image, popularity, brand voice, bestseller or target audience "
            "claim is generated."
        ),
    }
