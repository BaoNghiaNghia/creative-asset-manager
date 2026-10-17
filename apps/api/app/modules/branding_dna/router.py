"""Production-safe Branding DNA Foundation API.

This module never fetches external shop URLs, holds browser credentials or
starts scans. A dedicated agent/control protocol will be built in Phase 2.
"""
from __future__ import annotations
import re
from urllib.parse import urlsplit, urlunsplit, unquote
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")
LABEL = re.compile(r"^[\w\- '().&]{2,90}$", re.UNICODE)
SAFE_SEGMENT = re.compile(r"^[a-zA-Z0-9._%-]{2,120}$")

class ShopCreate(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=90)
    platform: str = Field(pattern="^(amazon|etsy|shopify)$")
    storefront_url: str = Field(min_length=12, max_length=2048)
    region: str = Field(default="US", min_length=2, max_length=10)
    access_mode: str = Field(default="public_observation", pattern="^(public_observation|owned|authorized)$")

def normalize_shop_url(platform: str, submitted: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(submitted.strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        path = unquote(parsed.path).strip("/")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid storefront URL") from exc
    if parsed.scheme != "https" or not hostname or parsed.port not in (None, 443):
        raise ValueError("Only HTTPS storefront URLs with the standard port are supported")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Storefront URL cannot include credentials, query or fragment")
    if any(x in path for x in ("..", "\\", "%", "@")):
        raise ValueError("Invalid storefront path")
    if platform == "etsy":
        if hostname not in {"etsy.com", "www.etsy.com"}:
            raise ValueError("Etsy shop must be on etsy.com")
        parts = path.split("/")
        if len(parts) != 2 or parts[0].lower() != "shop" or not SAFE_SEGMENT.fullmatch(parts[1]):
            raise ValueError("Use an Etsy /shop/{shop_name} URL")
        key = parts[1].casefold()
        canonical = f"https://www.etsy.com/shop/{parts[1]}"
    elif platform == "amazon":
        if not re.fullmatch(r"(?:www\.)?amazon\.(?:com|co\.uk|ca|de|fr|es|it|co\.jp)", hostname):
            raise ValueError("Unsupported Amazon marketplace host")
        parts = path.split("/")
        if len(parts) < 2 or parts[0].lower() != "stores" or not SAFE_SEGMENT.fullmatch(parts[1]):
            raise ValueError("Use an Amazon /stores/{store_name}/... URL")
        if any(not SAFE_SEGMENT.fullmatch(p) for p in parts):
            raise ValueError("Invalid Amazon storefront path")
        # Store names alone do not prove seller identity. Keep the URL as a draft
        # until an adapter resolves the stable seller/store ID and marketplace.
        key = hostname + ":" + path.casefold()
        canonical = urlunsplit(("https", hostname, "/" + path, "", ""))
    else:
        if not (hostname.endswith(".myshopify.com") and hostname.count(".") == 2):
            raise ValueError("For pilot use the verified *.myshopify.com domain")
        if path not in {"", "collections/all", "products"}:
            raise ValueError("Use the root Shopify storefront URL")
        key = hostname
        canonical = f"https://{hostname}/"
    return canonical, key

def present(row: BrandShopModel) -> dict:
    return {
        "id": row.id, "display_name": row.display_name, "platform": row.platform,
        "region": row.region, "storefront_url": row.storefront_url,
        "access_mode": row.access_mode, "status": row.status,
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
    }

@router.get("/status")
def status(session: Session = Depends(get_db),
           principal: CurrentPrincipal = Depends(READ)):
    tenant = principal.active_tenant_id
    shops = session.scalar(select(func.count()).select_from(BrandShopModel).where(BrandShopModel.tenant_id == tenant)) or 0
    agents = session.scalar(select(func.count()).select_from(BrandWorkerModel).where(BrandWorkerModel.tenant_id == tenant)) or 0
    from app.modules.branding_dna.worker_router import worker_online
    online_agents = sum(worker_online(row) for row in session.scalars(
        select(BrandWorkerModel).where(BrandWorkerModel.tenant_id == tenant,
                                     BrandWorkerModel.enabled.is_(True))
    ))
    return {
        "phase": "worker_control_plane", "shop_count": shops, "registered_workers": agents,
        "online_workers": online_agents,
        "scanner_ready": False, "scan_enabled": False,
        "blockers": ["scan_task_dispatch_not_deployed", "marketplace_access_not_verified",
                     "profile_pipeline_not_deployed"],
    }

@router.get("/shops")
def list_shops(session: Session = Depends(get_db),
               principal: CurrentPrincipal = Depends(READ)):
    rows = session.scalars(select(BrandShopModel).where(
        BrandShopModel.tenant_id == principal.active_tenant_id
    ).order_by(BrandShopModel.created_at.desc()).limit(200)).all()
    return {"items": [present(row) for row in rows], "count": len(rows)}

@router.get("/scout-agents/{agent_id}/shops")
def list_shops_for_paired_scout(
    agent_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    """Read-only tenant-scoped Shop Library for a *paired* Windows Scout Manager.

    Reuse its existing RRUGC Scout pairing; never expose shops across
    tenants, create authorization documents or schedule scans.
    """
    from app.modules.realistic_review_ugc.scout_automation import RrugcAutoScoutService
    from app.modules.realistic_review_ugc.service import RrugcError

    bearer = (authorization or "").split(" ", 1)
    if len(bearer) != 2 or bearer[0].lower() != "bearer" or not bearer[1].strip():
        raise HTTPException(401, detail="Paired Scout token required")
    try:
        scout = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=bearer[1].strip(),
        )
    except RrugcError:
        raise HTTPException(401, detail="Invalid paired Scout credentials") from None

    rows = session.scalars(select(BrandShopModel).where(
        BrandShopModel.tenant_id == scout.tenant_id,
        BrandShopModel.platform.in_(("amazon", "etsy")),
    ).order_by(BrandShopModel.created_at.desc()).limit(200)).all()
    # This is an informational control-plane contract, NOT authorization to
    # crawl. A shop's access_mode is self-declared, not independent evidence of
    # marketplace permission. A connected Scout must not infer job readiness.
    return {"items": [present(row) for row in rows], "count": len(rows),
            "scan_enabled": False,
            "dispatch": {
                "state": "disabled",
                "reason": "scan_task_dispatch_not_deployed",
                "active_scan_jobs": 0,
                "rights_verification_required": True,
                "marketplace_authorization_verified": False,
            }}


@router.post("/shops", status_code=201)
def add_shop(body: ShopCreate, session: Session = Depends(get_db),
             principal: CurrentPrincipal = Depends(CONFIGURE)):
    if body.display_name is not None and (body.display_name != body.display_name.strip() or not LABEL.fullmatch(body.display_name)):
        raise HTTPException(422, detail="Invalid shop display name")
    region = body.region.upper()
    if not re.fullmatch(r"[A-Z]{2,10}", region):
        raise HTTPException(422, detail="Invalid region")
    try:
        url, key = normalize_shop_url(body.platform, body.storefront_url)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    inferred_name = key.split(":")[-1].split("/")[-1] if body.platform == "amazon" else (key.split(".")[0] if body.platform == "shopify" else key)
    display_name = body.display_name or inferred_name[:90]
    shop = BrandShopModel(
        tenant_id=principal.active_tenant_id, platform=body.platform, region=region,
        canonical_shop_key=key, storefront_url=url, display_name=display_name,
        access_mode=body.access_mode, status="draft", created_by=principal.actor_id
    )
    session.add(shop)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="Shop is already registered for this tenant and region") from exc
    session.refresh(shop)
    return present(shop)

@router.get("/workers")
def list_workers(session: Session = Depends(get_db),
                 principal: CurrentPrincipal = Depends(READ)):
    rows = session.scalars(select(BrandWorkerModel).where(
        BrandWorkerModel.tenant_id == principal.active_tenant_id
    ).limit(100)).all()
    from app.modules.branding_dna.worker_router import worker_online
    return {"items": [
        {"id": row.id, "label": row.label,
         "status": row.status if worker_online(row) or not row.enabled else "offline",
         "enabled": row.enabled, "online": worker_online(row),
         "agent_version": row.agent_version,
         "last_heartbeat": row.last_heartbeat.isoformat() if row.last_heartbeat else None}
        for row in rows
    ]}

@router.post("/shops/{shop_id}/scan-runs")
def disabled_scan(shop_id: str, session: Session = Depends(get_db),
                  principal: CurrentPrincipal = Depends(CONFIGURE)):
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == shop_id, BrandShopModel.tenant_id == principal.active_tenant_id
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    raise HTTPException(409, detail={"code": "scanner_not_enrolled",
        "message": "No authorized Branding DNA scanner is available. Scan was not started."})
