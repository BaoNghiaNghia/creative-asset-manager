"""Immutable fact-outline versions and two-person review, with stale-input guard.

No model generation, image usage authorization, product verification or final
Brand DNA publication occurs at these endpoints.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.dna_draft_router import preview_dna_draft
from app.modules.branding_dna.evidence_router import shop_for_tenant
from app.modules.branding_dna.dna_version_model import (
    BrandDnaOutlineReviewModel, BrandDnaOutlineVersionModel,
)
from app.modules.branding_dna.model import BrandShopModel

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-versions"])
MANAGE = require_permission("ai_provider.configure")


class CreateOutlineVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class OutlineReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    decision: Literal["needs_changes", "fact_outline_reviewed", "rejected"]
    note: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def note_has_no_control_characters(self):
        if any(ord(char) < 32 for char in self.note):
            raise ValueError("Control characters are not permitted in a review")
        return self


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def current_outline(shop_id: str, session: Session, principal: CurrentPrincipal) -> dict:
    return preview_dna_draft(shop_id, Response(), session=session, principal=principal)


def find_version(session: Session, tenant: str, shop_id: str, version_id: str):
    version = session.scalar(select(BrandDnaOutlineVersionModel).where(
        BrandDnaOutlineVersionModel.id == version_id,
        BrandDnaOutlineVersionModel.tenant_id == tenant,
        BrandDnaOutlineVersionModel.shop_id == shop_id,
    ))
    if version is None:
        raise HTTPException(404, detail="DNA outline version not found")
    return version


def latest_reviews(session: Session, version_ids: list[str]) -> dict:
    if not version_ids:
        return {}
    rank = select(
        BrandDnaOutlineReviewModel.id.label("review_id"),
        func.row_number().over(
            partition_by=BrandDnaOutlineReviewModel.version_id,
            order_by=(
                BrandDnaOutlineReviewModel.created_at.desc(),
                BrandDnaOutlineReviewModel.id.desc(),
            ),
        ).label("ordinal"),
    ).where(BrandDnaOutlineReviewModel.version_id.in_(version_ids)).subquery()
    reviews = session.scalars(
        select(BrandDnaOutlineReviewModel)
        .join(rank, BrandDnaOutlineReviewModel.id == rank.c.review_id)
        .where(rank.c.ordinal == 1)
    ).all()
    return {review.version_id: review for review in reviews}


def present(version, latest, current_fingerprint: str, *, with_outline=False):
    result = {
        "id": version.id,
        "shop_id": version.shop_id,
        "version_number": version.version_number,
        "input_fingerprint": version.input_fingerprint,
        "content_sha256": version.content_sha256,
        "created_at": version.created_at.isoformat(),
        "created_by": version.created_by,
        "status": "current" if version.input_fingerprint == current_fingerprint else "stale",
        "review": ({
            "id": latest.id, "decision": latest.decision, "note": latest.note,
            "reviewed_by": latest.reviewed_by, "created_at": latest.created_at.isoformat(),
        } if latest else None),
        "is_final_brand_dna": False,
        "dna_generation_enabled": False,
        "image_rights_verified": False,
        "dispatchable": False,
    }
    if with_outline:
        # Check integrity before returning even though mutations are not exposed.
        if hashlib.sha256(version.outline_json.encode("utf-8")).hexdigest() != version.content_sha256:
            raise HTTPException(409, detail="Stored outline integrity mismatch")
        result["outline"] = json.loads(version.outline_json)
    return result


@router.get("/shops/{shop_id}/dna-draft/versions")
def list_outline_versions(
    shop_id: str, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    tenant = principal.active_tenant_id
    shop_for_tenant(session, tenant, shop_id)
    current = current_outline(shop_id, session, principal)
    versions = session.scalars(
        select(BrandDnaOutlineVersionModel).where(
            BrandDnaOutlineVersionModel.tenant_id == tenant,
            BrandDnaOutlineVersionModel.shop_id == shop_id,
        ).order_by(BrandDnaOutlineVersionModel.version_number.desc()).limit(25)
    ).all()
    review_by_version = latest_reviews(session, [v.id for v in versions])
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [
            present(v, review_by_version.get(v.id), current["input_fingerprint"])
            for v in versions
        ],
        "count": session.scalar(
            select(func.count()).select_from(BrandDnaOutlineVersionModel).where(
                BrandDnaOutlineVersionModel.tenant_id == tenant,
                BrandDnaOutlineVersionModel.shop_id == shop_id,
            )
        ) or 0,
        "final_brand_dna_enabled": False,
    }


@router.get("/shops/{shop_id}/dna-draft/versions/{version_id}")
def read_outline_version(
    shop_id: str, version_id: str, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    tenant = principal.active_tenant_id
    shop_for_tenant(session, tenant, shop_id)
    version = find_version(session, tenant, shop_id, version_id)
    latest = latest_reviews(session, [version.id]).get(version.id)
    current = current_outline(shop_id, session, principal)
    response.headers["Cache-Control"] = "no-store"
    return present(version, latest, current["input_fingerprint"], with_outline=True)


@router.post("/shops/{shop_id}/dna-draft/versions", status_code=201)
def create_outline_version(
    shop_id: str, body: CreateOutlineVersion, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    tenant = principal.active_tenant_id
    shop_for_tenant(session, tenant, shop_id)
    # Serialize shop-scoped version numbers on PostgreSQL. SQLite is protected
    # by database UNIQUE constraints and converts concurrent conflicts into 409.
    session.scalar(select(BrandShopModel.id).where(
        BrandShopModel.id == shop_id, BrandShopModel.tenant_id == tenant,
    ).with_for_update())
    current = current_outline(shop_id, session, principal)
    if current["input_fingerprint"] != body.input_fingerprint:
        raise HTTPException(409, detail="Source fingerprint changed; refresh preview")
    if current["status"] != "evidence_outline" or current["blockers"]:
        raise HTTPException(409, detail="Verified source evidence incomplete")
    previous = session.scalar(
        select(BrandDnaOutlineVersionModel).where(
            BrandDnaOutlineVersionModel.tenant_id == tenant,
            BrandDnaOutlineVersionModel.shop_id == shop_id,
            BrandDnaOutlineVersionModel.input_fingerprint == body.input_fingerprint,
        )
    )
    if previous:
        response.status_code = 200
        response.headers["Cache-Control"] = "no-store"
        return {**present(previous, latest_reviews(session,[previous.id]).get(previous.id),
                          body.input_fingerprint), "replayed": True}
    next_number = (session.scalar(select(func.max(
        BrandDnaOutlineVersionModel.version_number,
    )).where(
        BrandDnaOutlineVersionModel.tenant_id == tenant,
        BrandDnaOutlineVersionModel.shop_id == shop_id,
    )) or 0) + 1
    payload = canonical({
        "shop": current["shop"],
        "input_fingerprint": current["input_fingerprint"],
        "verified_listing_count": current["verified_listing_count"],
        "observed_categories": current["observed_categories"],
        "verified_product_examples": current["verified_product_examples"],
        "context_references": current["context_references"],
        "unresolved_sections": current["unresolved_sections"],
        "trust_rule": current["trust_rule"],
        "model_invoked": False,
        "final_brand_dna": False,
        "image_rights_verified": False,
    })
    row = BrandDnaOutlineVersionModel(
        tenant_id=tenant, shop_id=shop_id, version_number=next_number,
        input_fingerprint=body.input_fingerprint,
        content_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        outline_json=payload, created_by=principal.actor_id,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="Concurrent outline version change; refresh") from exc
    session.refresh(row)
    response.headers["Cache-Control"] = "no-store"
    return {**present(row, None, body.input_fingerprint), "replayed": False}


@router.post("/shops/{shop_id}/dna-draft/versions/{version_id}/reviews", status_code=201)
def review_outline_version(
    shop_id: str, version_id: str, body: OutlineReviewDecision, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    tenant = principal.active_tenant_id
    shop_for_tenant(session, tenant, shop_id)
    version = find_version(session, tenant, shop_id, version_id)
    if version.created_by == principal.actor_id:
        raise HTTPException(403, detail="Author cannot review own DNA outline")
    if hashlib.sha256(version.outline_json.encode("utf-8")).hexdigest() != version.content_sha256:
        raise HTTPException(409, detail="Stored outline integrity mismatch")
    session.scalar(select(BrandDnaOutlineVersionModel.id).where(
        BrandDnaOutlineVersionModel.id == version.id,
    ).with_for_update())
    current = current_outline(shop_id, session, principal)
    if (current["input_fingerprint"] != version.input_fingerprint
            or current["status"] != "evidence_outline" or current["blockers"]):
        raise HTTPException(409, detail="DNA source changed or verification is incomplete")
    prior = latest_reviews(session, [version.id]).get(version.id)
    if prior and (prior.decision, prior.note) == (body.decision, body.note):
        response.status_code = 200
        response.headers["Cache-Control"] = "no-store"
        return {
            "id": prior.id, "decision": prior.decision,
            "replayed": True, "is_final_brand_dna": False,
        }
    event = BrandDnaOutlineReviewModel(
        version_id=version.id, decision=body.decision,
        note=body.note, reviewed_by=principal.actor_id,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    response.headers["Cache-Control"] = "no-store"
    return {
        "id": event.id, "decision": event.decision,
        "replayed": False, "is_final_brand_dna": False,
        "dna_generation_enabled": False, "dispatchable": False,
    }
