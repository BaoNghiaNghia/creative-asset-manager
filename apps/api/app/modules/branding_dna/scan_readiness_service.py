"""Shared read-only Branding DNA marketplace readiness; never issues jobs or rights.

A declaration is an operator statement, not verified ownership, a marketplace
authorization or a dispatch permit. Paired Scouts and CAM admins use the same
blocked-state computation to avoid contradicting one another.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.access_review_model import BrandShopAccessReviewModel
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.task_model import BrandScanTaskModel


QUEUE_STATUSES = ("staged", "pending", "leased", "retry", "blocked", "completed", "cancelled")


def shop_queue_metrics(
    session: Session, *, tenant_id: str, shop_ids: list[str], now: datetime | None = None,
) -> dict[str, dict[str, int]]:
    """Tenant-scoped, aggregate-only queue health; never claims or mutates tasks."""
    if not shop_ids:
        return {}
    clock = now or datetime.now(timezone.utc)
    queue = {
        shop_id: {**{status: 0 for status in QUEUE_STATUSES},
                  "expired_leases": 0, "due_retries": 0}
        for shop_id in shop_ids
    }
    scope = (
        BrandScanTaskModel.tenant_id == tenant_id,
        BrandScanTaskModel.shop_id.in_(shop_ids),
    )
    for shop_id, status, count in session.execute(
        select(BrandScanTaskModel.shop_id, BrandScanTaskModel.status, func.count())
        .where(*scope)
        .group_by(BrandScanTaskModel.shop_id, BrandScanTaskModel.status)
    ).all():
        if status in QUEUE_STATUSES:
            queue[shop_id][status] = int(count)
    for shop_id, expired, due in session.execute(
        select(
            BrandScanTaskModel.shop_id,
            func.count().filter(and_(
                BrandScanTaskModel.status == "leased",
                or_(BrandScanTaskModel.lease_until.is_(None),
                    BrandScanTaskModel.lease_until <= clock),
            )),
            func.count().filter(and_(
                BrandScanTaskModel.status == "retry",
                BrandScanTaskModel.attempts < BrandScanTaskModel.max_attempts,
                or_(BrandScanTaskModel.retry_after.is_(None),
                    BrandScanTaskModel.retry_after <= clock),
            )),
        )
        .where(*scope)
        .group_by(BrandScanTaskModel.shop_id)
    ).all():
        queue[shop_id]["expired_leases"] = int(expired)
        queue[shop_id]["due_retries"] = int(due)
    return queue


def latest_shop_declarations(
    session: Session, *, tenant_id: str, shop_ids: list[str],
) -> dict[str, BrandShopAccessDeclarationModel]:
    if not shop_ids:
        return {}
    ranked = select(
        BrandShopAccessDeclarationModel.id.label("declaration_id"),
        func.row_number().over(
            partition_by=BrandShopAccessDeclarationModel.shop_id,
            order_by=(
                BrandShopAccessDeclarationModel.created_at.desc(),
                BrandShopAccessDeclarationModel.id.desc(),
            ),
        ).label("priority"),
    ).where(
        BrandShopAccessDeclarationModel.tenant_id == tenant_id,
        BrandShopAccessDeclarationModel.shop_id.in_(shop_ids),
    ).subquery()
    rows = session.scalars(
        select(BrandShopAccessDeclarationModel)
        .join(ranked, BrandShopAccessDeclarationModel.id == ranked.c.declaration_id)
        .where(ranked.c.priority == 1)
    ).all()
    return {row.shop_id: row for row in rows}


def latest_declaration_reviews(
    session: Session, *, tenant_id: str, declaration_ids: list[str],
) -> dict[str, BrandShopAccessReviewModel]:
    if not declaration_ids:
        return {}
    ranked = select(
        BrandShopAccessReviewModel.id.label("review_id"),
        func.row_number().over(
            partition_by=BrandShopAccessReviewModel.declaration_id,
            order_by=(
                BrandShopAccessReviewModel.created_at.desc(),
                BrandShopAccessReviewModel.id.desc(),
            ),
        ).label("priority"),
    ).where(
        BrandShopAccessReviewModel.tenant_id == tenant_id,
        BrandShopAccessReviewModel.declaration_id.in_(declaration_ids),
    ).subquery()
    rows = session.scalars(
        select(BrandShopAccessReviewModel)
        .join(ranked, BrandShopAccessReviewModel.id == ranked.c.review_id)
        .where(ranked.c.priority == 1)
    ).all()
    return {row.declaration_id: row for row in rows}


def shop_scan_readiness(session: Session, *, tenant_id: str,
                        limit: int = 200, include_queue: bool = False,
                        now: datetime | None = None) -> list[dict]:
    """A bounded tenant-scoped snapshot; every shop remains non-dispatchable."""
    shops = session.scalars(
        select(BrandShopModel)
        .where(
            BrandShopModel.tenant_id == tenant_id,
            BrandShopModel.platform.in_(("amazon", "etsy")),
        )
        .order_by(BrandShopModel.created_at, BrandShopModel.id)
        .limit(min(max(limit, 1), 200))
    ).all()
    ids = [shop.id for shop in shops]
    if not ids:
        return []
    declarations = latest_shop_declarations(session, tenant_id=tenant_id, shop_ids=ids)
    reviews = latest_declaration_reviews(
        session, tenant_id=tenant_id, declaration_ids=[d.id for d in declarations.values()]
    )
    staged = dict(session.execute(
        select(BrandScanTaskModel.shop_id, func.count())
        .where(
            BrandScanTaskModel.tenant_id == tenant_id,
            BrandScanTaskModel.shop_id.in_(ids),
            BrandScanTaskModel.status == "staged",
        )
        .group_by(BrandScanTaskModel.shop_id)
    ).all())
    queue = shop_queue_metrics(
        session, tenant_id=tenant_id, shop_ids=ids, now=now,
    ) if include_queue else {}
    items: list[dict] = []
    for shop in shops:
        declaration = declarations.get(shop.id)
        review = reviews.get(declaration.id) if declaration else None
        blockers = [
            "scan_task_dispatch_not_deployed",
            "marketplace_authorization_not_verified",
        ]
        # Registry relationship is descriptive metadata, not access control.
        # Draft/competitor/unknown shops may be researched or opened normally.
        # Deliberately paused shops remain withheld from any future dispatcher.
        if shop.status == "paused":
            blockers.append("shop_paused")
        if int(staged.get(shop.id, 0)) == 0:
            blockers.append("no_staged_scan_tasks")
        blockers.append(
            "etsy_written_permission_not_verified"
            if shop.platform == "etsy"
            else "amazon_agent_policy_review_not_verified"
        )
        item = {
            "shop_id": shop.id,
            "platform": shop.platform,
            "display_name": shop.display_name,
            "status": "blocked",
            "staged_tasks": int(staged.get(shop.id, 0)),
            "blockers": blockers,
            "declaration_relationship": declaration.relationship if declaration else None,
            "declaration_status": declaration.status if declaration else None,
            "review_decision": review.decision if review else None,
        }
        if include_queue:
            item["queue"] = queue[shop.id]
        items.append(item)
    return items