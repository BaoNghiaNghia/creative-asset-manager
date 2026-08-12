from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.inventory.daily.service import DailyRunBlocked, InventoryDailyRunService
from app.modules.inventory.permissions import (
    INVENTORY_FINALIZE_PERMISSION,
    INVENTORY_READ_PERMISSION,
    INVENTORY_REVIEW_PERMISSION,
)
from app.modules.inventory.review.service import InventoryReviewService

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


class CorrectRequest(BaseModel):
    values: dict = Field(default_factory=dict)


class FinalizeRequest(BaseModel):
    force: bool = False
    reason: str | None = Field(default=None, max_length=2000)


def _view(row):
    return {
        "id": row.id,
        "document_id": row.document_id,
        "line_id": row.line_id,
        "reason_code": row.reason_code,
        "status": row.status,
        "original_value": row.original_value_json,
        "suggested_value": row.suggested_value_json,
        "final_value": row.final_value_json,
        "reviewer_id": row.reviewer_id,
        "reviewed_at": row.reviewed_at,
    }


def _daily_view(row):
    return {
        "id": row.id,
        "business_date": row.business_date,
        "status": row.status,
        "ready": row.ready,
        "finalized": row.finalized,
        "forced": row.forced,
        "blockers": row.snapshot.get("blockers", []),
        "report": row.snapshot,
        "finalized_at": row.finalized_at,
        "finalized_by": row.finalized_by,
    }


@router.get("/reviews")
def list_reviews(
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    return {
        "items": [
            _view(row)
            for row in InventoryReviewService(SessionLocal).list(principal.active_tenant_id)
        ]
    }


@router.get("/reviews/{review_id}")
def get_review(
    review_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    row = InventoryReviewService(SessionLocal).get(principal.active_tenant_id, review_id)
    if row is None:
        raise HTTPException(404, detail={"code": "inventory_review_not_found"})
    return _view(row)


def _mutate(review_id, action, values, principal):
    try:
        return _view(
            InventoryReviewService(SessionLocal).mutate(
                principal.active_tenant_id, review_id, action, principal.actor_id, values
            )
        )
    except LookupError:
        raise HTTPException(404, detail={"code": "inventory_review_not_found"})
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc)})


@router.post("/reviews/{review_id}/approve")
def approve(
    review_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION)),
):
    return _mutate(review_id, "approve", None, principal)


@router.post("/reviews/{review_id}/correct")
def correct(
    review_id: str,
    body: CorrectRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION)),
):
    return _mutate(review_id, "correct", body.values, principal)


@router.post("/reviews/{review_id}/request-reupload")
def request_reupload(
    review_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION)),
):
    return _mutate(review_id, "request_reupload", None, principal)


@router.get("/daily-runs/{business_date}")
def get_daily_run(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    result = InventoryDailyRunService(SessionLocal).get(
        principal.active_tenant_id, business_date
    )
    if result is None:
        raise HTTPException(404, detail={"code": "inventory_daily_run_not_found"})
    return _daily_view(result)


@router.post("/daily-runs/{business_date}/finalize")
def finalize_daily_run(
    business_date: date,
    body: FinalizeRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION)),
):
    service = InventoryDailyRunService(SessionLocal)
    try:
        result = service.finalize(
            principal.active_tenant_id,
            business_date,
            actor_id=principal.actor_id,
            force=body.force,
            reason=body.reason,
        )
    except DailyRunBlocked as exc:
        raise HTTPException(
            409,
            detail={
                "code": "inventory_daily_run_not_ready",
                "message": "Inventory day cannot be finalized while blockers remain",
                "report": exc.snapshot,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc
    return _daily_view(result)
