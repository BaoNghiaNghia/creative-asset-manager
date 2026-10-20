"""Tenant-scoped shop relationship review requests. No dispatch authorization."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.access_review_model import BrandShopAccessReviewModel
from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel
from app.modules.branding_dna.scout_session_model import BrandScoutSessionModel
from app.modules.branding_dna.scout_session_router import online as paired_scout_online
from app.modules.branding_dna.worker_router import worker_online
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
from app.modules.branding_dna.scan_readiness_service import (
    shop_scan_readiness, latest_shop_declarations, latest_declaration_reviews,
)

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-access-declarations"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")


class ShopRelationshipDeclaration(BaseModel):
    model_config = {"extra": "forbid"}
    relationship: Literal["owned", "authorized", "competitor_research", "unknown"]
    evidence_reference: str = Field(default="", max_length=240)


class DocumentaryReview(BaseModel):
    model_config = {"extra": "forbid"}
    declaration_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    decision: Literal["needs_documentation", "rejected", "documentation_noted"]
    note: str = Field(min_length=8, max_length=500)


def present_review(row: BrandShopAccessReviewModel) -> dict:
    return {
        "id": row.id, "shop_id": row.shop_id, "declaration_id": row.declaration_id,
        "decision": row.decision, "note": row.note,
        "reviewed_by": row.reviewed_by, "created_at": row.created_at.isoformat(),
        "rights_verified": False, "dispatchable": False,
    }


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
    ids = session.scalars(
        select(BrandShopModel.id).where(BrandShopModel.tenant_id == tenant)
        .order_by(BrandShopModel.created_at, BrandShopModel.id).limit(200)
    ).all()
    latest = latest_shop_declarations(session, tenant_id=tenant, shop_ids=list(ids))
    response.headers["Cache-Control"] = "no-store"
    return {"items": [present_declaration(latest[id]) for id in ids if id in latest],
            "rights_verified": False, "scan_enabled": False}


@router.get("/shops/scan-readiness")
def read_shop_scan_readiness(
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """An admin-only, read-only view, never a dispatch or rights-grant route."""
    items = shop_scan_readiness(session, tenant_id=principal.active_tenant_id, include_queue=True)
    queue_totals = {key: sum(item["queue"][key] for item in items)
                    for key in ("staged", "pending", "leased", "retry", "blocked",
                                "completed", "cancelled", "expired_leases", "due_retries")}
    response.headers["Cache-Control"] = "no-store"
    return {
        "version": 1, "scan_enabled": False, "ready_count": 0,
        "blocked_count": len(items), "shop_count": len(items),
        "staged_tasks": sum(item["staged_tasks"] for item in items),
        "queue_totals": queue_totals,
        "items": items,
    }


@router.get("/workers/dispatch-health")
def read_worker_dispatch_health(
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Operator-only readiness for agents; strictly observational.

    Gives the reason for no job allocation without sending scan commands or
    claiming that an Amazon/Etsy shop has been independently authorized.
    """
    tenant = principal.active_tenant_id
    clock = datetime.now(timezone.utc)
    workers = session.scalars(
        select(BrandWorkerModel)
        .where(BrandWorkerModel.tenant_id == tenant)
        .order_by(BrandWorkerModel.created_at.desc())
        .limit(200)
    ).all()
    dedicated = {
        "registered": len(workers), "online_idle": 0,
        "online_paused": 0, "online_unhealthy": 0,
        "offline": 0, "disabled": 0,
    }
    for worker in workers:
        if not worker.enabled:
            dedicated["disabled"] += 1
        elif not worker_online(worker, now=clock):
            dedicated["offline"] += 1
        elif worker.status == "idle":
            dedicated["online_idle"] += 1
        elif worker.status == "paused":
            dedicated["online_paused"] += 1
        else:
            dedicated["online_unhealthy"] += 1

    paired_rows = session.execute(
        select(BrandScoutSessionModel, RrugcScoutAgentModel.active)
        .join(
            RrugcScoutAgentModel,
            (BrandScoutSessionModel.agent_id == RrugcScoutAgentModel.id)
            & (BrandScoutSessionModel.tenant_id == RrugcScoutAgentModel.tenant_id),
        )
        .where(BrandScoutSessionModel.tenant_id == tenant)
        .order_by(BrandScoutSessionModel.last_seen_at.desc())
        .limit(200)
    ).all()
    paired_online_count = sum(
        bool(active and paired_scout_online(record.last_seen_at, clock))
        for record, active in paired_rows
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "version": 1,
        "scan_enabled": False,
        "dispatch_state": "disabled",
        "reason": "scan_pipeline_not_deployed",
        "marketplace_authorization_verified": False,
        "dedicated_workers": dedicated,
        "paired_scouts": {
            "registered": len(paired_rows),
            "connected_waiting": paired_online_count,
            "offline": len(paired_rows) - paired_online_count,
        },
        "message": (
            "Connected agents cannot receive browser jobs until independent "
            "shop authorization and a verified permitted adapter are deployed."
        ),
    }


@router.get("/shops/access-reviews")
def list_access_reviews(
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    """Show the latest documentary decisions to privileged curators only."""
    tenant = principal.active_tenant_id
    ids = session.scalars(
        select(BrandShopModel.id).where(BrandShopModel.tenant_id == tenant)
        .order_by(BrandShopModel.created_at, BrandShopModel.id).limit(200)
    ).all()
    declarations = latest_shop_declarations(
        session, tenant_id=tenant, shop_ids=list(ids)
    )
    reviews = latest_declaration_reviews(
        session, tenant_id=tenant, declaration_ids=[d.id for d in declarations.values()]
    )
    response.headers["Cache-Control"] = "no-store"
    items = []
    for shop_id in ids:
        row = declarations.get(shop_id)
        if row and row.status == "pending_review":
            latest = reviews.get(row.id)
            items.append({
                "shop_id": shop_id,
                "declaration_id": row.id,
                "relationship": row.relationship,
                "evidence_reference": row.evidence_reference,
                "declared_by": row.declared_by,
                "review": present_review(latest) if latest else None,
                "rights_verified": False,
                "dispatchable": False,
            })
    return {"items": items, "rights_verified": False, "scan_enabled": False}


@router.post("/shops/{shop_id}/access-reviews", status_code=201)
def submit_documentary_review(
    shop_id: str, body: DocumentaryReview, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    tenant = principal.active_tenant_id
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == shop_id, BrandShopModel.tenant_id == tenant,
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    declaration = session.scalar(select(BrandShopAccessDeclarationModel).where(
        BrandShopAccessDeclarationModel.id == body.declaration_id,
        BrandShopAccessDeclarationModel.shop_id == shop_id,
        BrandShopAccessDeclarationModel.tenant_id == tenant,
    ))
    if declaration is None:
        raise HTTPException(404, detail="Access declaration not found")
    latest = latest_shop_declarations(session, tenant_id=tenant, shop_ids=[shop_id]).get(shop_id)
    if latest is None or latest.id != declaration.id or latest.status != "pending_review":
        raise HTTPException(409, detail="Only the latest pending declaration can be reviewed")
    if declaration.declared_by == principal.actor_id:
        raise HTTPException(403, detail="The author cannot review their own declaration")
    note = body.note.strip()
    if len(note) < 8 or note != body.note or any(ord(c) < 32 for c in note):
        raise HTTPException(422, detail="A valid review note is required")
    prior = latest_declaration_reviews(
        session, tenant_id=tenant, declaration_ids=[declaration.id]
    ).get(declaration.id)
    if prior and prior.decision == body.decision and prior.note == note:
        response.status_code = 200
        response.headers["Cache-Control"] = "no-store"
        return {**present_review(prior), "replayed": True}
    record = BrandShopAccessReviewModel(
        tenant_id=tenant, shop_id=shop_id, declaration_id=declaration.id,
        decision=body.decision, note=note, reviewed_by=principal.actor_id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    response.headers["Cache-Control"] = "no-store"
    # A documentary review NEVER changes shop.status/access_mode,
    # dispatch flags, staged tasks or marketplace authorization.
    return {**present_review(record), "replayed": False}


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
