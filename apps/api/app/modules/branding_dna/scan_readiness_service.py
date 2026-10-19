"""Shared read-only Branding DNA marketplace readiness; never issues jobs or rights.

A declaration is an operator statement, not verified ownership, a marketplace
authorization or a dispatch permit. Paired Scouts and CAM admins use the same
blocked-state computation to avoid contradicting one another.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.task_model import BrandScanTaskModel


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


def shop_scan_readiness(session: Session, *, tenant_id: str, limit: int = 200) -> list[dict]:
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
    staged = dict(session.execute(
        select(BrandScanTaskModel.shop_id, func.count())
        .where(
            BrandScanTaskModel.tenant_id == tenant_id,
            BrandScanTaskModel.shop_id.in_(ids),
            BrandScanTaskModel.status == "staged",
        )
        .group_by(BrandScanTaskModel.shop_id)
    ).all())
    items: list[dict] = []
    for shop in shops:
        declaration = declarations.get(shop.id)
        blockers = [
            "scan_task_dispatch_not_deployed",
            "marketplace_authorization_not_verified",
        ]
        if declaration and declaration.relationship == "competitor_research":
            blockers.append("shop_competitor_research_only")
        elif declaration and declaration.status == "pending_review" and declaration.relationship in (
            "owned", "authorized",
        ):
            blockers.append("shop_access_pending_verification")
        else:
            blockers.append("shop_access_not_declared")
        if shop.status != "active":
            blockers.append("shop_not_active")
        if int(staged.get(shop.id, 0)) == 0:
            blockers.append("no_staged_scan_tasks")
        blockers.append(
            "etsy_written_permission_not_verified"
            if shop.platform == "etsy"
            else "amazon_agent_policy_review_not_verified"
        )
        items.append({
            "shop_id": shop.id,
            "platform": shop.platform,
            "display_name": shop.display_name,
            "status": "blocked",
            "staged_tasks": int(staged.get(shop.id, 0)),
            "blockers": blockers,
            "declaration_relationship": declaration.relationship if declaration else None,
            "declaration_status": declaration.status if declaration else None,
        })
    return items
