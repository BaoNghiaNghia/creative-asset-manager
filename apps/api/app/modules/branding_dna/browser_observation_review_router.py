"""Append-only, independent human review of quarantined DOM observations.

A human judgement is never independent source verification, an image-use
license or authorization for a new marketplace browser job.
"""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.browser_observation_model import BrandBrowserObservationModel
from app.modules.branding_dna.browser_observation_review_model import BrandBrowserObservationReviewModel
from app.modules.branding_dna.evidence_router import shop_for_tenant

router = APIRouter(prefix="/api/v1/branding-dna",
                   tags=["branding-dna-browser-observation-reviews"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")


class BrowserObservationReviewInput(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    decision: Literal["needs_authorized_source", "metadata_consistent", "rejected"]
    rights_basis: Literal["unknown", "consent_reference_recorded", "permission_missing"]
    evidence_reference: str | None = Field(
        default=None, min_length=8, max_length=160,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]*$",
    )
    note: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def validate_claims(self):
        if self.rights_basis == "consent_reference_recorded" and not self.evidence_reference:
            raise ValueError("A documentary consent reference is required")
        if self.rights_basis != "consent_reference_recorded" and self.evidence_reference:
            raise ValueError("Consent reference needs a documentary consent basis")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.note):
            raise ValueError("Control characters are not permitted")
        return self


def locate_observation(
    session: Session, tenant: str, shop_id: str, report_id: str,
    *, lock: bool = False,
) -> BrandBrowserObservationModel:
    shop_for_tenant(session, tenant, shop_id)
    query = select(BrandBrowserObservationModel).where(
        BrandBrowserObservationModel.id == report_id,
        BrandBrowserObservationModel.shop_id == shop_id,
        BrandBrowserObservationModel.tenant_id == tenant,
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        raise HTTPException(404, detail="Browser pilot report not found")
    return row


def present(row: BrandBrowserObservationReviewModel) -> dict:
    return {
        "id": row.id, "shop_id": row.shop_id,
        "report_id": row.observation_id, "decision": row.decision,
        "rights_basis": row.rights_basis,
        "evidence_reference": row.evidence_reference,
        "note": row.note, "reviewed_by": row.reviewed_by,
        "created_at": row.created_at.isoformat(),
        "source_verified": False, "rights_verified": False,
        "sales_verified": False, "training_approved": False,
        "scan_enabled": False, "dispatchable": False,
        "jobs_created": 0,
    }


@router.get("/browser-observations/review-queue")
def browser_observation_review_queue(
    response: Response,
    state: Literal["pending_independent_review", "needs_authorized_source",
                   "metadata_consistent", "rejected"] | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Operator inbox, partitioned by tenant; latest review never verifies facts."""
    tenant = principal.active_tenant_id
    last_decision = (
        select(BrandBrowserObservationReviewModel.decision)
        .where(
            BrandBrowserObservationReviewModel.tenant_id
            == BrandBrowserObservationModel.tenant_id,
            BrandBrowserObservationReviewModel.shop_id
            == BrandBrowserObservationModel.shop_id,
            BrandBrowserObservationReviewModel.observation_id
            == BrandBrowserObservationModel.id,
        )
        .order_by(
            BrandBrowserObservationReviewModel.created_at.desc(),
            BrandBrowserObservationReviewModel.id.desc(),
        ).limit(1).correlate(BrandBrowserObservationModel).scalar_subquery()
    )
    display_state = func.coalesce(last_decision, "pending_independent_review")
    criteria = (BrandBrowserObservationModel.tenant_id == tenant,)
    if state is not None:
        criteria += (display_state == state,)
    count = session.scalar(
        select(func.count()).select_from(BrandBrowserObservationModel)
        .where(*criteria)
    ) or 0
    entries = session.execute(
        select(BrandBrowserObservationModel, display_state.label("review_state"))
        .where(*criteria)
        .order_by(BrandBrowserObservationModel.created_at.desc(),
                  BrandBrowserObservationModel.id.desc())
        .limit(limit).offset(offset)
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [{**{
            "id": report.id, "shop_id": report.shop_id,
            "platform": report.platform,
            "report_key": report.report_key,
            "observed_count": len(json.loads(report.items_json)),
            "created_at": report.created_at.isoformat(),
            "review_state": review_state,
        }, "source_verified": False, "rights_verified": False,
           "scan_enabled": False} for report, review_state in entries],
        "count": count, "limit": limit, "offset": offset,
        "state_filter": state,
        "scan_enabled": False, "dispatchable": False,
        "source_verified": False, "rights_verified": False,
    }


@router.get("/shops/{shop_id}/browser-observations/{report_id}/reviews")
def list_browser_observation_reviews(
    shop_id: str, report_id: str, response: Response,
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    locate_observation(session, tenant, shop_id, report_id)
    rows = session.scalars(select(BrandBrowserObservationReviewModel).where(
        BrandBrowserObservationReviewModel.tenant_id == tenant,
        BrandBrowserObservationReviewModel.shop_id == shop_id,
        BrandBrowserObservationReviewModel.observation_id == report_id,
    ).order_by(
        BrandBrowserObservationReviewModel.created_at.desc(),
        BrandBrowserObservationReviewModel.id.desc(),
    ).limit(limit)).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [present(item) for item in rows], "report_id": report_id,
        "latest_decision": rows[0].decision if rows else "pending_independent_review",
        "review_count_returned": len(rows),
        "source_verified": False, "rights_verified": False,
        "training_approved": False, "scan_enabled": False,
    }


@router.post("/shops/{shop_id}/browser-observations/{report_id}/reviews",
             status_code=201)
def submit_browser_observation_review(
    shop_id: str, report_id: str, body: BrowserObservationReviewInput,
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    tenant = principal.active_tenant_id
    report = locate_observation(session, tenant, shop_id, report_id, lock=True)
    if report.imported_by == principal.actor_id:
        raise HTTPException(403, detail="The pilot report importer cannot review their own report")
    criteria = (
        BrandBrowserObservationReviewModel.tenant_id == tenant,
        BrandBrowserObservationReviewModel.shop_id == shop_id,
        BrandBrowserObservationReviewModel.observation_id == report_id,
    )
    previous = session.scalar(select(BrandBrowserObservationReviewModel)
        .where(*criteria).order_by(
            BrandBrowserObservationReviewModel.created_at.desc(),
            BrandBrowserObservationReviewModel.id.desc(),
        ).limit(1))
    response.headers["Cache-Control"] = "no-store"
    if previous is not None and (
        previous.decision == body.decision
        and previous.rights_basis == body.rights_basis
        and previous.evidence_reference == body.evidence_reference
        and previous.note == body.note
    ):
        response.status_code = 200
        return {**present(previous), "replayed": True}
    row = BrandBrowserObservationReviewModel(
        tenant_id=tenant, shop_id=shop_id, observation_id=report_id,
        decision=body.decision, rights_basis=body.rights_basis,
        evidence_reference=body.evidence_reference, note=body.note,
        reviewed_by=principal.actor_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**present(row), "replayed": False}
