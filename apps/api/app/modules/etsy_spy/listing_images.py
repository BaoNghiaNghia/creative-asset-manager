"""Tenant-isolated ImageGen listing output gallery.

Device uploads come ONLY from a paired Image Agent, after local PNG SHA
verification. The bytes are stored privately in the existing ImageGen
managed Drive destination. Browser users receive only authenticated CAM
image URLs, never a Windows path, Google Drive ID or device credential.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections import defaultdict

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from .model import EtsySpyListingImage as ListingImage, EtsySpyPicked as Picked
from .service import authenticate_agent

listing_image_router = APIRouter(tags=["etsy-listing-images"])
READ = require_permission("etsy_spy.read")
_LISTING = re.compile(r"^[0-9]{5,20}$")
_PACK = re.compile(r"^sp-[0-9a-f]{40}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_GARMENTS = frozenset({"C1717", "G500", "G180", "G185"})
_MAX_BODY = 112 * 1024 * 1024
_MAX_PNG = 80 * 1024 * 1024


def verified_identity(listing_key: str, pack_id: str, slot: int, garment: str | None = None):
    if not _LISTING.fullmatch(str(listing_key)) or not _PACK.fullmatch(str(pack_id)) \
       or not isinstance(slot, int) or isinstance(slot, bool) or not 1 <= slot <= 10:
        raise HTTPException(422, "Invalid ImageGen listing output identity")
    if garment is not None and garment not in _GARMENTS:
        raise HTTPException(422, "Invalid garment model")


async def _json_payload(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > _MAX_BODY:
            raise HTTPException(413, "ImageGen listing output exceeds size limit")
    try:
        import json
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid ImageGen listing output receipt") from None
    if not isinstance(value, dict):
        raise HTTPException(422, "ImageGen listing output object required")
    return value


@listing_image_router.post("/image-agents/{agent_id}/listing-outputs")
async def save_listing_output(
    agent_id: str, request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Idempotent per pack/slot; queued roles are never counted as outputs."""
    agent = authenticate_agent(db, agent_id, authorization, image_only=True)
    payload = await _json_payload(request)
    listing = str(payload.get("listing_id") or "")
    pack = str(payload.get("pack_id") or "")
    garment = str(payload.get("garment_code") or "").upper()
    slot = payload.get("slot")
    verified_identity(listing, pack, slot, garment)
    sha = str(payload.get("sha256") or "")
    if not _SHA.fullmatch(sha):
        raise HTTPException(422, "ImageGen output SHA256 required")
    if db.get(Picked, (agent.tenant_id, listing)) is None:
        raise HTTPException(409, "Listing is not currently Picked")
    key = (agent.tenant_id, listing, pack, slot)
    previous = db.get(ListingImage, key)
    if previous is not None:
        if previous.output_sha256 != sha or previous.garment_code != garment:
            raise HTTPException(409, "Immutable ImageGen output slot changed")
        return {"ok": True, "already_saved": True, "listing_id": listing,
                "garment_code": garment, "pack_id": pack, "slot": slot, "sha256": sha}
    encoded = payload.get("png_base64")
    if not isinstance(encoded, str) or len(encoded) > (_MAX_PNG * 4 // 3 + 16):
        raise HTTPException(413, "ImageGen PNG receipt exceeds limit")
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Invalid ImageGen output PNG") from None
    if not 57 <= len(png) <= _MAX_PNG or hashlib.sha256(png).hexdigest() != sha:
        raise HTTPException(422, "Saved ImageGen PNG does not match SHA256")
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(422, "ImageGen PNG required")
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    if not 256 <= width <= 8192 or not 256 <= height <= 8192:
        raise HTTPException(422, "Invalid ImageGen output dimensions")
    source_job = str(payload.get("source_job_id") or "")
    if source_job and not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", source_job):
        raise HTTPException(422, "Invalid source ImageGen job ID")
    # The Drive helper makes a small private thumbnail, verifies the
    # configured account/folder, and stores the original by content SHA.
    from .imagegen_drive import store_listing_output_to_drive
    remote = await store_listing_output_to_drive(
        tenant_id=agent.tenant_id, pack_id=pack,
        slot=slot, digest=sha, png=png)
    # Recheck Picked after I/O to prevent a late unpick from authorizing
    # a new visible gallery entry. Re-pick can restore its previous history.
    if db.get(Picked, (agent.tenant_id, listing)) is None:
        raise HTTPException(409, "Listing was unpicked during upload")
    row = ListingImage(
        tenant_id=agent.tenant_id, listing_key=listing, pack_id=pack, slot=slot,
        garment_code=garment, output_sha256=sha,
        thumbnail_sha256=remote["thumbnail_sha256"],
        drive_file_id=remote["drive_file_id"],
        drive_thumbnail_id=remote["drive_thumbnail_id"],
        native_width=width, native_height=height,
        source_job_id=source_job or None)
    db.add(row)
    db.commit()
    return {"ok": True, "already_saved": False, "listing_id": listing,
            "garment_code": garment, "pack_id": pack, "slot": slot, "sha256": sha}


def listing_badges(db: Session, tenant_id: str, listing_keys: set[str]):
    """One batched query for the currently visible Picked table page."""
    if not listing_keys:
        return {}
    counts = defaultdict(dict)
    rows = db.execute(select(
        ListingImage.listing_key, ListingImage.garment_code,
        func.count()
    ).where(ListingImage.tenant_id == tenant_id,
            ListingImage.listing_key.in_(listing_keys))
      .group_by(ListingImage.listing_key, ListingImage.garment_code))
    for listing_key, garment, quantity in rows:
        counts[listing_key][garment] = int(quantity)
    return {key: [{"garmentCode": garment, "count": n}
                  for garment, n in sorted(group.items())]
            for key, group in counts.items()}


@listing_image_router.get("/listings/{listing_key}/listing-images")
def read_listing_gallery(
    listing_key: str, principal: CurrentPrincipal = Depends(READ),
    db: Session = Depends(get_db),
):
    if not _LISTING.fullmatch(listing_key):
        raise HTTPException(422, "Invalid Etsy Listing ID")
    tenant = principal.active_tenant_id
    if db.get(Picked, (tenant, listing_key)) is None:
        raise HTTPException(404, "Picked listing not found")
    rows = db.scalars(select(ListingImage).where(
        ListingImage.tenant_id == tenant, ListingImage.listing_key == listing_key
    ).order_by(ListingImage.garment_code, ListingImage.created_at,
               ListingImage.pack_id, ListingImage.slot)).all()
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        image = {
            "packId": row.pack_id, "slot": row.slot,
            "sha256": row.output_sha256,
            "width": row.native_width, "height": row.native_height,
            "savedAt": row.created_at,
            "thumbnailUrl": f"/api/v1/etsy-spy/listing-images/{row.pack_id}/{row.slot}/thumbnail",
            "originalUrl": f"/api/v1/etsy-spy/listing-images/{row.pack_id}/{row.slot}/original",
        }
        groups[row.garment_code].append(image)
    return {"listingKey": listing_key, "total": len(rows),
            "groups": [{"garmentCode": name, "count": len(images), "images": images}
                       for name, images in sorted(groups.items())]}


@listing_image_router.get("/listing-images/{pack_id}/{slot}/{quality}")
async def read_listing_output_image(
    pack_id: str, slot: int, quality: str,
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    if not _PACK.fullmatch(pack_id) or slot < 1 or slot > 10 or quality not in {"thumbnail", "original"}:
        raise HTTPException(404, "ImageGen listing output not found")
    tenant = principal.active_tenant_id
    row = db.scalar(select(ListingImage).where(
        ListingImage.tenant_id == tenant, ListingImage.pack_id == pack_id,
        ListingImage.slot == slot,
        select(Picked.listing_key).where(
            Picked.tenant_id == tenant,
            Picked.listing_key == ListingImage.listing_key).exists(),
    ))
    if row is None:
        raise HTTPException(404, "ImageGen listing output not found")
    from .imagegen_drive import read_design_from_drive
    sha = row.thumbnail_sha256 if quality == "thumbnail" else row.output_sha256
    drive_id = row.drive_thumbnail_id if quality == "thumbnail" else row.drive_file_id
    # Release SELECT transaction before slow Drive I/O.
    db.rollback()
    png = await read_design_from_drive(
        tenant_id=tenant, asset_id=f"{pack_id}:listing-{slot:02d}:{quality}",
        remote_id=drive_id, sha256=sha,
        max_bytes=2 * 1024 * 1024 if quality == "thumbnail" else _MAX_PNG)
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300, must-revalidate",
                             "Vary": "Cookie", "X-Content-Type-Options": "nosniff"})
