"""Branding DNA authorized offline source preview (not a scraper)."""
from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.evidence_router import shop_for_tenant
from app.modules.branding_dna.shopify_csv_adapter import ShopifyCsvError, preview_shopify_csv
from app.modules.branding_dna.marketplace_export_adapter import (
    MarketplaceExportError, preview_etsy_listings_csv, preview_amazon_inventory_report,
)

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-source-preview"])
CONFIGURE = require_permission("ai_provider.configure")


class SourcePreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["shopify_products_csv", "etsy_listings_csv", "amazon_inventory_report"]
    csv_text: str = Field(min_length=1, max_length=2_000_000)


@router.post("/shops/{shop_id}/source-preview")
def preview_source_export(
    shop_id: str, body: SourcePreviewInput, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    shop = shop_for_tenant(session, principal.active_tenant_id, shop_id)
    if shop.access_mode not in {"owned", "authorized"}:
        raise HTTPException(403, detail="Shop must be owned or authorized for export preflight")
    valid_format = {
        "shopify": "shopify_products_csv",
        "etsy": "etsy_listings_csv",
        "amazon": "amazon_inventory_report",
    }
    if valid_format.get(shop.platform) != body.format:
        raise HTTPException(409, detail="Source format does not match the selected shop platform")
    if len(body.csv_text.encode("utf-8")) > 2_000_000:
        raise HTTPException(413, detail="source_csv_too_large")
    try:
        if body.format == "etsy_listings_csv":
            report = preview_etsy_listings_csv(body.csv_text)
        elif body.format == "amazon_inventory_report":
            report = preview_amazon_inventory_report(body.csv_text)
        else:
            # Legacy API contract kept for existing authorized Shopify shops.
            report = preview_shopify_csv(body.csv_text)
    except (ShopifyCsvError, MarketplaceExportError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {
        "shop_id": shop.id,
        "shop_platform": shop.platform,
        "shop_access_mode": shop.access_mode,
        **report,
        "preview_state": "imported_export_unverified",
    }
