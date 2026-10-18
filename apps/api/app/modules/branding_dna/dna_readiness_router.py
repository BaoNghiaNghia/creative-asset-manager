"""Evidence-only DNA Input Readiness: inspect, never infer or mutate DNA.

Shop documents are potentially private; this endpoint shares the RAG
management permission until dedicated knowledge RBAC exists.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.evidence_router import shop_for_tenant
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel, BrandListingImageModel
from app.modules.branding_dna.knowledge_model import BrandKnowledgeDocumentModel, BrandKnowledgeChunkModel
from app.modules.branding_dna.knowledge_service import citation_id

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-readiness"])
READ = require_permission("ai_provider.configure")


@router.get("/shops/{shop_id}/dna-inputs")
def preview_dna_inputs(
    shop_id: str, response: Response,
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    shop = shop_for_tenant(session, tenant, shop_id)
    criteria = (
        BrandListingSnapshotModel.tenant_id == tenant,
        BrandListingSnapshotModel.shop_id == shop_id,
    )
    source_rows = session.scalars(
        select(BrandListingSnapshotModel).where(*criteria)
        .order_by(BrandListingSnapshotModel.id).limit(3000)
    ).all()
    # The bounded query is NOT a coverage report for very large shops.
    all_listings_count = session.scalar(
        select(func.count()).select_from(BrandListingSnapshotModel).where(*criteria)
    ) or 0
    verified_rows = [x for x in source_rows if x.evidence_state == "verified"]
    gallery_count = session.scalar(select(func.count()).select_from(BrandListingImageModel).where(
        BrandListingImageModel.tenant_id == tenant,
        BrandListingImageModel.shop_id == shop_id,
    )) or 0
    documents = session.scalars(
        select(BrandKnowledgeDocumentModel).where(
            BrandKnowledgeDocumentModel.tenant_id == tenant,
            BrandKnowledgeDocumentModel.shop_id == shop_id,
        ).order_by(BrandKnowledgeDocumentModel.id).limit(60)
    ).all()
    approved = [d for d in documents if d.status == "approved"]
    approved_ids = [d.id for d in approved]
    first_chunks: dict[str, BrandKnowledgeChunkModel] = {}
    if approved_ids:
        for chunk in session.scalars(select(BrandKnowledgeChunkModel).where(
            BrandKnowledgeChunkModel.tenant_id == tenant,
            BrandKnowledgeChunkModel.shop_id == shop_id,
            BrandKnowledgeChunkModel.document_id.in_(approved_ids),
            BrandKnowledgeChunkModel.ordinal == 0,
        ).order_by(BrandKnowledgeChunkModel.document_id).limit(60)):
            first_chunks[chunk.document_id] = chunk
    sources = []
    for doc in approved[:20]:
        block = first_chunks.get(doc.id)
        sources.append({
            "document_id": doc.id, "filename": doc.filename,
            "category": doc.category, "source_note": doc.source_note,
            "sha256": doc.content_sha256,
            "citation": citation_id(doc.id, 0) if block is not None else None,
            "excerpt": block.content[:450] if block is not None else "",
            "trust": "human_approved_reference_not_verified_product_fact",
        })
    # The fingerprint is stable across UI refreshes, cannot contain text or
    # user-provided instructions, and deliberately changes when sources change.
    identity = {
        "tenant": tenant, "shop": shop.id, "platform": shop.platform,
        "documents": [(d.id, d.content_sha256) for d in approved],
        "verified_listing_sources": [(x.id, x.content_sha256) for x in verified_rows],
    }
    fingerprint = hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    blockers = ["verified_product_evidence_missing"] if not verified_rows else []
    if all_listings_count > len(source_rows):
        blockers.append("listing_sample_truncated")
    notices = []
    if not approved:
        notices.append("approved_shop_knowledge_missing_optional")
    if all_listings_count and not verified_rows:
        notices.append("imported_listings_are_unverified")
    notices.extend(("brand_picks_pipeline_pending", "best_seller_signals_pipeline_pending"))
    response.headers["Cache-Control"] = "no-store"
    return {
        "shop": {"id": shop.id, "display_name": shop.display_name, "platform": shop.platform},
        "source_counts": {
            "listing_snapshots": all_listings_count,
            "verified_listing_snapshots": len(verified_rows),
            "gallery_image_manifests": gallery_count,
            "knowledge_documents": len(documents),
            "approved_knowledge_documents": len(approved),
            "pending_knowledge_documents": sum(d.status == "pending_review" for d in documents),
        },
        "knowledge_sources": sources,
        "input_fingerprint": fingerprint,
        "blockers": blockers, "notices": notices,
        "dna_draft_ready": not blockers,
        "dna_generation_enabled": False, "automatic_dna_mutation_enabled": False,
        "retrieval_mode": "approved_lexical_v1",
        "best_seller_verified": False, "brand_pick_available": False,
        "trust_rule": "Shop knowledge is untrusted contextual reference, not product/sales/platform proof.",
    }
