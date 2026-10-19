"""Two-person, append-only assessment of imported evidence and rights paperwork.

An assessment never marks any listing or gallery image as verified. A source
adapter with independent provenance is required for actual verification.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.evidence_router import shop_for_tenant
from app.modules.branding_dna.evidence_assessment_model import BrandEvidenceAssessmentModel

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-evidence-review"])
CONFIGURE = require_permission("ai_provider.configure")


class EvidenceAssessmentRequest(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    decision: Literal["needs_independent_source", "metadata_consistent", "rejected"]
    rights_basis: Literal["unknown", "consent_reference_recorded", "permission_missing"]
    evidence_reference: str | None = Field(default=None, min_length=8, max_length=160,
                                           pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]*$")
    note: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def valid_recorded_basis(self):
        if self.rights_basis == "consent_reference_recorded" and not self.evidence_reference:
            raise ValueError("A documentary consent reference is required")
        if self.rights_basis != "consent_reference_recorded" and self.evidence_reference:
            raise ValueError("An evidence reference requires a recorded consent basis")
        if any(ord(ch) < 32 for ch in self.note):
            raise ValueError("Control characters are not permitted")
        return self


def locate_snapshot(session: Session, tenant: str, shop_id: str, snapshot_id: str):
    shop_for_tenant(session, tenant, shop_id)
    row = session.scalar(
        select(BrandListingSnapshotModel).where(
            BrandListingSnapshotModel.id == snapshot_id,
            BrandListingSnapshotModel.tenant_id == tenant,
            BrandListingSnapshotModel.shop_id == shop_id,
        )
    )
    if row is None:
        raise HTTPException(404, detail="Listing snapshot not found")
    return row


def present_assessment(row: BrandEvidenceAssessmentModel) -> dict:
    return {
        "id": row.id, "shop_id": row.shop_id, "snapshot_id": row.snapshot_id,
        "decision": row.decision, "rights_basis": row.rights_basis,
        "evidence_reference": row.evidence_reference, "note": row.note,
        "reviewed_by": row.reviewed_by, "created_at": row.created_at.isoformat(),
        "assessment_grants_listing_verification": False, "assessment_grants_image_rights": False,
        "scan_enabled": False, "dispatchable": False,
    }


@router.get("/shops/{shop_id}/listings/{snapshot_id}/assessments")
def list_evidence_assessments(
    shop_id: str, snapshot_id: str, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    tenant = principal.active_tenant_id
    locate_snapshot(session, tenant, shop_id, snapshot_id)
    rows = session.scalars(
        select(BrandEvidenceAssessmentModel).where(
            BrandEvidenceAssessmentModel.tenant_id == tenant,
            BrandEvidenceAssessmentModel.shop_id == shop_id,
            BrandEvidenceAssessmentModel.snapshot_id == snapshot_id,
        ).order_by(
            BrandEvidenceAssessmentModel.created_at.desc(),
            BrandEvidenceAssessmentModel.id.desc(),
        ).limit(100)
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [present_assessment(x) for x in rows],
        "assessment_grants_listing_verification": False, "assessment_grants_image_rights": False,
        "scan_enabled": False, "dispatchable": False,
    }


@router.post("/shops/{shop_id}/listings/{snapshot_id}/assessments", status_code=201)
def submit_evidence_assessment(
    shop_id: str, snapshot_id: str, body: EvidenceAssessmentRequest,
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    tenant = principal.active_tenant_id
    snapshot = locate_snapshot(session, tenant, shop_id, snapshot_id)
    if snapshot.imported_by == principal.actor_id:
        raise HTTPException(403, detail="The importer cannot assess their own evidence")
    # Row lock serializes the append-only audit decision for this listing.
    session.scalar(select(BrandListingSnapshotModel.id).where(
        BrandListingSnapshotModel.id == snapshot.id,
        BrandListingSnapshotModel.tenant_id == tenant,
        BrandListingSnapshotModel.shop_id == shop_id,
    ).with_for_update())
    previous = session.scalar(
        select(BrandEvidenceAssessmentModel).where(
            BrandEvidenceAssessmentModel.tenant_id == tenant,
            BrandEvidenceAssessmentModel.shop_id == shop_id,
            BrandEvidenceAssessmentModel.snapshot_id == snapshot_id,
        ).order_by(
            BrandEvidenceAssessmentModel.created_at.desc(),
            BrandEvidenceAssessmentModel.id.desc(),
        ).limit(1)
    )
    if previous is not None and (
        previous.decision == body.decision
        and previous.rights_basis == body.rights_basis
        and previous.evidence_reference == body.evidence_reference
        and previous.note == body.note
    ):
        response.headers["Cache-Control"] = "no-store"
        response.status_code = 200
        return {**present_assessment(previous), "replayed": True}
    entry = BrandEvidenceAssessmentModel(
        tenant_id=tenant, shop_id=shop_id, snapshot_id=snapshot_id,
        decision=body.decision, rights_basis=body.rights_basis,
        evidence_reference=body.evidence_reference,
        note=body.note, reviewed_by=principal.actor_id,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    response.headers["Cache-Control"] = "no-store"
    return {**present_assessment(entry), "replayed": False}
