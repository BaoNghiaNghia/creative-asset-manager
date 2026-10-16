"""Read and stage metadata verification tasks; no scan execution endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.task_service import _task_dict, stage_from_snapshot

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-scan-tasks"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")


class TaskStage(BaseModel):
    model_config = {"extra": "forbid"}
    snapshot_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


@router.post("/shops/{shop_id}/scan-tasks", status_code=201)
def stage_task(
    shop_id: str, body: TaskStage, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    task, replayed = stage_from_snapshot(
        session, tenant_id=principal.active_tenant_id, shop_id=shop_id,
        snapshot_id=body.snapshot_id, actor_id=principal.actor_id,
    )
    return {"item": _task_dict(task), "replayed": replayed,
            "scan_enabled": False, "dispatchable": False}


@router.get("/shops/{shop_id}/scan-tasks")
def list_scan_tasks(
    shop_id: str, limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == shop_id, BrandShopModel.tenant_id == tenant,
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    criteria = (BrandScanTaskModel.tenant_id == tenant, BrandScanTaskModel.shop_id == shop_id)
    count = session.scalar(select(func.count()).select_from(BrandScanTaskModel).where(*criteria)) or 0
    tasks = session.scalars(
        select(BrandScanTaskModel).where(*criteria)
        .order_by(BrandScanTaskModel.created_at.desc(), BrandScanTaskModel.id.desc())
        .offset(offset).limit(limit)
    ).all()
    return {
        "items": [_task_dict(row) for row in tasks],
        "count": count, "limit": limit, "offset": offset,
        "scan_enabled": False, "dispatchable": False,
    }


@router.post("/shops/{shop_id}/scan-tasks/{task_id}/cancel")
def cancel_staged_task(
    shop_id: str, task_id: str, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    task = session.scalar(select(BrandScanTaskModel).where(
        BrandScanTaskModel.id == task_id,
        BrandScanTaskModel.shop_id == shop_id,
        BrandScanTaskModel.tenant_id == principal.active_tenant_id,
    ).with_for_update())
    if task is None:
        raise HTTPException(404, detail="Scan task not found")
    if task.status != "staged":
        raise HTTPException(409, detail="Only non-dispatchable staged tasks can be cancelled")
    task.status = "cancelled"
    session.commit()
    return {"item": _task_dict(task), "scan_enabled": False}
