"""Tenant-scoped shop relationship review requests. No dispatch authorization."""
from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.model import BrandShopModel

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-access-declarations"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")


class ShopRelationshipDeclaration(BaseModel):
    model_config = {"extra": "forbid"}
    relationship: Literal["owned", "authorized", "competitor_research", "unknown"]
    evidence_reference: str = Field(default="", max_length=240)


def present_declaration(row: BrandShopAccessDeclarationModel) -> dict:
    return {
        "id": row.id, "shop_id": row.shop_id,
        "relationship": row.relationship, "evidence_reference": row.evidence_reference,
        "status": row.status, "declared_by": row.declared_by,
        "created_at": row.created_at.isoformat(),
        "rights_verified": False, "dispatchable": False,
    }


@router.get("/shops/access-declarations")
def latest_access_declarations(
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """At most one latest declaration per shop; all tenants stay isolated."""
    tenant = principal.active_tenant_id
    rows = session.scalars(
        select(BrandShopAccessDeclarationModel)
        .where(BrandShopAccessDeclarationModel.tenant_id == tenant)
        .order_by(BrandShopAccessDeclarationModel.created_at.desc(),
                  BrandShopAccessDeclarationModel.id.desc())
        .limit(2000)
    ).all()
    latest = {}
    for row in rows:
        if row.shop_id not in latest:
            latest[row.shop_id] = present_declaration(row)
        if len(latest) >= 200:
            break
    response.headers["Cache-Control"] = "no-store"
    return {"items": list(latest.values()), "rights_verified": False, "scan_enabled": False}


@router.post("/shops/{shop_id}/access-declarations", status_code=201)
def declare_shop_relationship(
    shop_id: str, body: ShopRelationshipDeclaration,
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    """Save operator statement, without upgrading the shop's effective access."""
    tenant = principal.active_tenant_id
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == shop_id,
        BrandShopModel.tenant_id == tenant,
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    reference = body.evidence_reference.strip()
    if reference != body.evidence_reference or any(ord(c) < 32 for c in reference):
        raise HTTPException(422, detail="Invalid document reference")
    if body.relationship in ("owned", "authorized") and len(reference) < 6:
        raise HTTPException(422, detail="Add an internal proof/document reference for review")
    if len(reference) > 240:
        raise HTTPException(422, detail="Document reference too long")

    latest = session.scalar(
        select(BrandShopAccessDeclarationModel).where(
            BrandShopAccessDeclarationModel.tenant_id == tenant,
            BrandShopAccessDeclarationModel.shop_id == shop_id,
        ).order_by(BrandShopAccessDeclarationModel.created_at.desc(),
                   BrandShopAccessDeclarationModel.id.desc()).limit(1)
    )
    if latest and latest.relationship == body.relationship and latest.evidence_reference == reference:
        response.status_code = 200
        response.headers["Cache-Control"] = "no-store"
        return {**present_declaration(latest), "replayed": True}

    state = "pending_review" if body.relationship in ("owned", "authorized") else "recorded_only"
    record = BrandShopAccessDeclarationModel(
        tenant_id=tenant, shop_id=shop_id, relationship=body.relationship,
        evidence_reference=reference, status=state, declared_by=principal.actor_id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    response.headers["Cache-Control"] = "no-store"
    # Do not modify BrandShopModel.access_mode or BrandShopModel.status.
    # A human declaration is never independently verified marketplace permission.
    return {**present_declaration(record), "replayed": False}
