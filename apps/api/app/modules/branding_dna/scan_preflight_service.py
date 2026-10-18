"""Read-only, fail-closed shop policy review preflight.

This service classifies *operator paperwork* only. No value returned by this
module constitutes provider permission or authorizes scan dispatch.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.branding_dna.knowledge_model import BrandKnowledgeDocumentModel
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.scan_authorization_model import BrandScanAuthorizationReviewModel

PLATFORM_SCOPES = {"amazon": "amazon_agent_policy", "etsy": "etsy_written_access"}
POLICY_STATES = ("current_human_review", "pending_human_review", "revoked",
                 "expired_or_changed", "rejected", "not_submitted")


def utc_datetime(value: datetime) -> datetime:
    # Datetimes are normalized to UTC on write. SQLite loses tz information
    # when re-reading a stored value, while PostgreSQL preserves it.
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def shop_identity_digest(shop: BrandShopModel) -> str:
    identity = "|".join((shop.platform, shop.region,
                         shop.canonical_shop_key, shop.storefront_url))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def human_review_current(
    row: BrandScanAuthorizationReviewModel,
    shop: BrandShopModel,
    document: BrandKnowledgeDocumentModel | None,
    now: datetime,
) -> bool:
    """Return a human-paperwork state, never a provider grant."""
    return bool(
        row.status == "human_reviewed"
        and row.policy_scope == PLATFORM_SCOPES.get(shop.platform)
        and row.platform == shop.platform
        and row.storefront_sha256 == shop_identity_digest(shop)
        and shop.access_mode in ("owned", "authorized")
        and row.submitted_by != row.reviewed_by
        and bool(row.reviewed_by)
        and row.reviewed_at is not None
        and utc_datetime(row.expires_at) > utc_datetime(now)
        and document is not None
        and document.tenant_id == row.tenant_id
        and document.shop_id == row.shop_id
        and document.id == row.document_id
        and document.status == "approved"
        and document.category == "policy"
        and document.content_sha256 == row.document_sha256
    )


def summarize_policy_reviews(
    session: Session, *, tenant_id: str, shop: BrandShopModel, now: datetime,
) -> dict:
    """Only aggregated, tenant-scoped metadata; no legal text or reviewer IDs."""
    rows = session.scalars(select(BrandScanAuthorizationReviewModel).where(
        BrandScanAuthorizationReviewModel.tenant_id == tenant_id,
        BrandScanAuthorizationReviewModel.shop_id == shop.id,
    ).order_by(BrandScanAuthorizationReviewModel.created_at.desc()).limit(40)).all()
    docs = session.scalars(select(BrandKnowledgeDocumentModel).where(
        BrandKnowledgeDocumentModel.tenant_id == tenant_id,
        BrandKnowledgeDocumentModel.shop_id == shop.id,
        BrandKnowledgeDocumentModel.id.in_(r.document_id for r in rows),
    )).all() if rows else []
    by_id = {doc.id: doc for doc in docs}

    counts = {
        "total": len(rows), "current_human_review": 0, "pending_human_review": 0,
        "revoked": 0, "expired_or_changed": 0, "rejected": 0,
    }
    for row in rows:
        if human_review_current(row, shop, by_id.get(row.document_id), now):
            counts["current_human_review"] += 1
        elif row.status == "pending_review":
            counts["pending_human_review"] += 1
        elif row.status == "revoked":
            counts["revoked"] += 1
        elif row.status == "rejected":
            counts["rejected"] += 1
        else:
            counts["expired_or_changed"] += 1

    state = next((x for x in POLICY_STATES if counts.get(x, 0)), "not_submitted")
    return {
        "state": state, "counts": counts,
        "human_review_current": counts["current_human_review"] > 0,
        "marketplace_authorization_verified": False,
        "dispatchable": False,
    }
