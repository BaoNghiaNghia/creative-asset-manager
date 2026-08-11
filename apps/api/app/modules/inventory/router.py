from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.inventory.permissions import INVENTORY_READ_PERMISSION, INVENTORY_REVIEW_PERMISSION
from app.modules.inventory.review.service import InventoryReviewService
from app.core.database import SessionLocal

router = APIRouter(prefix="/api/inventory", tags=["inventory"])
class CorrectRequest(BaseModel):
    values: dict = Field(default_factory=dict)
def _view(row):
    return {"id": row.id, "document_id": row.document_id, "line_id": row.line_id, "reason_code": row.reason_code, "status": row.status, "original_value": row.original_value_json, "suggested_value": row.suggested_value_json, "final_value": row.final_value_json, "reviewer_id": row.reviewer_id, "reviewed_at": row.reviewed_at}
@router.get('/reviews')
def list_reviews(principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    return {"items": [_view(row) for row in InventoryReviewService(SessionLocal).list(principal.active_tenant_id)]}
@router.get('/reviews/{review_id}')
def get_review(review_id: str, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    row=InventoryReviewService(SessionLocal).get(principal.active_tenant_id, review_id)
    if row is None: raise HTTPException(404, detail={"code":"inventory_review_not_found"})
    return _view(row)
def _mutate(review_id, action, values, principal):
    try: return _view(InventoryReviewService(SessionLocal).mutate(principal.active_tenant_id, review_id, action, principal.actor_id, values))
    except LookupError: raise HTTPException(404, detail={"code":"inventory_review_not_found"})
    except ValueError as exc: raise HTTPException(422, detail={"code":str(exc)})
@router.post('/reviews/{review_id}/approve')
def approve(review_id: str, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION))): return _mutate(review_id,'approve',None,principal)
@router.post('/reviews/{review_id}/correct')
def correct(review_id: str, body: CorrectRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION))): return _mutate(review_id,'correct',body.values,principal)
@router.post('/reviews/{review_id}/request-reupload')
def request_reupload(review_id: str, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION))): return _mutate(review_id,'request_reupload',None,principal)
