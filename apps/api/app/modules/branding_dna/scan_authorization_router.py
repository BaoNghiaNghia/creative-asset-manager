"""Manual policy-paperwork review for Amazon/Etsy shops.

An operator's recorded review is never independent confirmation that the
marketplace permits automated access. No dispatch, URL fetch or worker task
transition occurs here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandShopModel, now_utc
from app.modules.branding_dna.knowledge_model import BrandKnowledgeDocumentModel
from app.modules.branding_dna.scan_authorization_model import BrandScanAuthorizationReviewModel
from app.modules.branding_dna.scan_preflight_service import human_review_current, shop_identity_digest

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-scan-authorization"])
ADMIN = require_permission("ai_provider.configure")
MAX_REVIEWS_PER_SHOP = 40
POLICY_SCOPES = {"etsy": "etsy_written_access", "amazon": "amazon_agent_policy"}


class SubmitPolicyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    expires_at: datetime


class DecidePolicyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(acknowledge|reject|revoke)$")
    note: str = Field(min_length=10, max_length=500)


def _aware(value: datetime, *, stored: bool = False) -> datetime:
    # SQLite drops timezone info on read; stored timestamps are always UTC.
    # Incoming user-submitted timestamps must explicitly include a timezone.
    if value.tzinfo is None or value.utcoffset() is None:
        if not stored:
            raise HTTPException(422, detail="review_expiry_requires_timezone")
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _shop(session: Session, tenant: str, shop_id: str) -> BrandShopModel:
    row = session.scalar(select(BrandShopModel).where(
        BrandShopModel.tenant_id == tenant, BrandShopModel.id == shop_id,
    ))
    if row is None:
        raise HTTPException(404, detail="Shop not found")
    return row


def _fingerprint(shop: BrandShopModel) -> str:
    return shop_identity_digest(shop)


def _record(row: BrandScanAuthorizationReviewModel, shop: BrandShopModel,
            document: BrandKnowledgeDocumentModel | None, now: datetime) -> dict:
    valid = human_review_current(row, shop, document, now)
    return {
        "id": row.id, "shop_id": row.shop_id, "document_id": row.document_id,
        "policy_scope": row.policy_scope, "status": row.status,
        "human_review_current": bool(valid),
        "document_sha256": row.document_sha256,
        "submitted_by": row.submitted_by, "reviewed_by": row.reviewed_by,
        "review_note": row.review_note,
        "created_at": row.created_at.isoformat(),
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "revoked_by": row.revoked_by,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "revocation_note": row.revocation_note,
        "expires_at": row.expires_at.isoformat(),
        # A reviewed policy file alone never certifies marketplace/AI rights.
        "marketplace_authorization_verified": False,
        "dispatchable": False, "scan_enabled": False,
    }


def _read_review(session: Session, tenant: str, shop_id: str, review_id: str,
                 lock: bool = False) -> BrandScanAuthorizationReviewModel:
    q = select(BrandScanAuthorizationReviewModel).where(
        BrandScanAuthorizationReviewModel.tenant_id == tenant,
        BrandScanAuthorizationReviewModel.shop_id == shop_id,
        BrandScanAuthorizationReviewModel.id == review_id,
    )
    row = session.scalar(q.with_for_update() if lock else q)
    if row is None:
        raise HTTPException(404, detail="Scan policy review not found")
    return row


@router.post("/shops/{shop_id}/scan-policy-reviews", status_code=201)
def submit_scan_policy(
    shop_id: str, body: SubmitPolicyReview, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ADMIN),
):
    tenant = principal.active_tenant_id
    # Serialize quota and immutable document identity checks at shop level.
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.tenant_id == tenant, BrandShopModel.id == shop_id,
    ).with_for_update())
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    if shop.platform not in POLICY_SCOPES or shop.access_mode not in ("owned", "authorized"):
        raise HTTPException(409, detail="Shop is not eligible for policy paperwork review")
    now = now_utc()
    expires = _aware(body.expires_at)
    if not now + timedelta(hours=1) < expires <= now + timedelta(days=365):
        raise HTTPException(422, detail="review_expiry_outside_accepted_window")
    doc = session.scalar(select(BrandKnowledgeDocumentModel).where(
        BrandKnowledgeDocumentModel.id == str(body.document_id),
        BrandKnowledgeDocumentModel.shop_id == shop_id,
        BrandKnowledgeDocumentModel.tenant_id == tenant,
        BrandKnowledgeDocumentModel.status == "approved",
        BrandKnowledgeDocumentModel.category == "policy",
    ))
    if doc is None:
        raise HTTPException(409, detail="approved_shop_policy_document_required")
    count = session.scalar(select(func.count()).select_from(BrandScanAuthorizationReviewModel)
                           .where(BrandScanAuthorizationReviewModel.tenant_id == tenant,
                                  BrandScanAuthorizationReviewModel.shop_id == shop_id)) or 0
    if count >= MAX_REVIEWS_PER_SHOP:
        raise HTTPException(429, detail="scan_policy_review_history_limit")
    existing = session.scalar(select(BrandScanAuthorizationReviewModel).where(
        BrandScanAuthorizationReviewModel.tenant_id == tenant,
        BrandScanAuthorizationReviewModel.shop_id == shop_id,
        BrandScanAuthorizationReviewModel.document_id == doc.id,
    ))
    if existing is not None:
        raise HTTPException(409, detail="scan_policy_document_already_submitted")
    review = BrandScanAuthorizationReviewModel(
        tenant_id=tenant, shop_id=shop_id, document_id=doc.id,
        document_sha256=doc.content_sha256, platform=shop.platform,
        storefront_sha256=_fingerprint(shop),
        policy_scope=POLICY_SCOPES[shop.platform],
        status="pending_review", submitted_by=principal.actor_id,
        expires_at=expires,
    )
    session.add(review)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="scan_policy_concurrent_submission") from exc
    session.refresh(review)
    response.headers["Cache-Control"] = "no-store"
    return {"item": _record(review, shop, doc, now), "review_only": True}


@router.get("/shops/{shop_id}/scan-policy-reviews")
def list_scan_policies(
    shop_id: str, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ADMIN),
):
    tenant = principal.active_tenant_id
    shop = _shop(session, tenant, shop_id)
    reviews = session.scalars(select(BrandScanAuthorizationReviewModel).where(
        BrandScanAuthorizationReviewModel.tenant_id == tenant,
        BrandScanAuthorizationReviewModel.shop_id == shop_id,
    ).order_by(BrandScanAuthorizationReviewModel.created_at.desc(),
               BrandScanAuthorizationReviewModel.id.desc()).limit(MAX_REVIEWS_PER_SHOP)).all()
    doc_ids = [r.document_id for r in reviews]
    documents = {
        row.id: row for row in session.scalars(select(BrandKnowledgeDocumentModel).where(
            BrandKnowledgeDocumentModel.tenant_id == tenant,
            BrandKnowledgeDocumentModel.shop_id == shop_id,
            BrandKnowledgeDocumentModel.id.in_(doc_ids),
        )).all()
    } if doc_ids else {}
    now = now_utc()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [_record(r, shop, documents.get(r.document_id), now) for r in reviews],
        "count": len(reviews), "marketplace_authorization_verified": False,
        "dispatchable": False, "scan_enabled": False,
        "warning": "Human-reviewed paperwork is not independent marketplace authorization.",
    }


@router.post("/shops/{shop_id}/scan-policy-reviews/{review_id}/decision")
def decide_scan_policy(
    shop_id: str, review_id: UUID, body: DecidePolicyReview,
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ADMIN),
):
    tenant = principal.active_tenant_id
    if len(body.note.strip()) < 10:
        raise HTTPException(422, detail="scan_policy_review_note_too_short")
    shop = _shop(session, tenant, shop_id)
    row = _read_review(session, tenant, shop_id, str(review_id), lock=True)
    if body.action in ("acknowledge", "reject"):
        if row.status != "pending_review":
            raise HTTPException(409, detail="review_already_decided")
        if row.submitted_by == principal.actor_id:
            raise HTTPException(403, detail="independent_reviewer_required")
        if body.action == "acknowledge":
            document = session.scalar(select(BrandKnowledgeDocumentModel).where(
                BrandKnowledgeDocumentModel.tenant_id == tenant,
                BrandKnowledgeDocumentModel.shop_id == shop_id,
                BrandKnowledgeDocumentModel.id == row.document_id,
                BrandKnowledgeDocumentModel.category == "policy",
                BrandKnowledgeDocumentModel.status == "approved",
                BrandKnowledgeDocumentModel.content_sha256 == row.document_sha256,
            ))
            if (document is None or _aware(row.expires_at, stored=True) <= now_utc()
                    or row.platform != shop.platform
                    or row.storefront_sha256 != _fingerprint(shop)
                    or shop.access_mode not in ("owned", "authorized")):
                raise HTTPException(409, detail="scan_policy_review_evidence_expired_or_changed")
        row.status = "human_reviewed" if body.action == "acknowledge" else "rejected"
    elif body.action == "revoke":
        if row.status != "human_reviewed":
            raise HTTPException(409, detail="only_human_reviewed_policy_can_be_revoked")
        row.status = "revoked"
        row.revoked_by = principal.actor_id
        row.revoked_at = now_utc()
        row.revocation_note = body.note.strip()
    else:
        raise HTTPException(422, detail="invalid_policy_review_action")
    if body.action != "revoke":
        row.review_note = body.note.strip()
        row.reviewed_by = principal.actor_id
        row.reviewed_at = now_utc()
    doc = session.scalar(select(BrandKnowledgeDocumentModel).where(
        BrandKnowledgeDocumentModel.tenant_id == tenant,
        BrandKnowledgeDocumentModel.shop_id == shop_id,
        BrandKnowledgeDocumentModel.id == row.document_id,
    ))
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"item": _record(row, shop, doc, now_utc()),
            "review_only": True, "dispatchable": False}
