from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.modules.inventory.persistence_model import InventoryDocumentModel, InventoryItemModel, InventoryLineModel, InventoryReviewEventModel, InventoryReviewModel

class InventoryReviewService:
    def __init__(self, session_factory: sessionmaker): self.session_factory = session_factory
    def list(self, tenant_id: str):
        with self.session_factory() as s:
            return list(s.scalars(select(InventoryReviewModel).where(InventoryReviewModel.tenant_id == tenant_id).order_by(InventoryReviewModel.created_at, InventoryReviewModel.id)))
    def get(self, tenant_id: str, review_id: str):
        with self.session_factory() as s:
            return s.scalar(select(InventoryReviewModel).where(InventoryReviewModel.tenant_id == tenant_id, InventoryReviewModel.id == review_id))
    def mutate(self, tenant_id: str, review_id: str, action: str, actor_id: str, correction: dict | None = None):
        with self.session_factory() as s:
            review = s.scalar(select(InventoryReviewModel).where(InventoryReviewModel.tenant_id == tenant_id, InventoryReviewModel.id == review_id).with_for_update() if s.bind.dialect.name == 'postgresql' else select(InventoryReviewModel).where(InventoryReviewModel.tenant_id == tenant_id, InventoryReviewModel.id == review_id))
            if review is None: raise LookupError('inventory_review_not_found')
            existing = s.scalar(select(InventoryReviewEventModel).where(InventoryReviewEventModel.tenant_id == tenant_id, InventoryReviewEventModel.review_id == review_id, InventoryReviewEventModel.action == action))
            if existing is not None: return review
            line = s.scalar(select(InventoryLineModel).where(InventoryLineModel.tenant_id == tenant_id, InventoryLineModel.id == review.line_id)) if review.line_id else None
            document = s.scalar(select(InventoryDocumentModel).where(InventoryDocumentModel.tenant_id == tenant_id, InventoryDocumentModel.id == review.document_id))
            if action == 'correct':
                data = dict(correction or {})
                item_id = data.get('item_id')
                if item_id and s.scalar(select(InventoryItemModel).where(InventoryItemModel.tenant_id == tenant_id, InventoryItemModel.id == item_id)) is None: raise ValueError('inventory_review_item_not_found')
                if line:
                    review.final_value_json = data
                    if item_id: line.item_id = item_id
                    line.normalized_values_json = {**line.normalized_values_json, **data}
                    line.validation_status = 'corrected'
                review.status = 'approved'
            elif action == 'approve':
                review.status = 'approved'
            elif action == 'request_reupload':
                review.status = 'rejected'
                if document: document.status = 'needs_reupload'
            else: raise ValueError('inventory_review_action_invalid')
            review.reviewer_id = actor_id; review.reviewed_at = datetime.now(timezone.utc)
            s.add(InventoryReviewEventModel(tenant_id=tenant_id, review_id=review.id, action=action, actor_id=actor_id, payload_json=dict(correction or {})))
            if document and action != 'request_reupload':
                pending = s.scalar(select(InventoryReviewModel).where(InventoryReviewModel.tenant_id == tenant_id, InventoryReviewModel.document_id == document.id, InventoryReviewModel.status.in_(('pending','in_review'))))
                if pending is None: document.status = 'approved'; document.approved_by = actor_id; document.approved_at = datetime.now(timezone.utc)
            s.commit(); return review
