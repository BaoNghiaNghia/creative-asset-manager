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
from sqlalchemy import select, func, case, update
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
_PLACEMENT = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_DRIVE_ROOT = "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl"
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


async def _ensure_pack_manifest(
    db: Session, tenant: str, listing: str, pack: str, garment: str,
    design_sha: str, source_sha: str, placement: str,
) -> str | None:
    """Manifest is written only after ten distinct SHA-bound saved slots.

    Retries, including a lost ACK after the last upload, recreate neither
    images nor a manifest; they only reconcile the original immutable pack.
    """
    rows = db.scalars(select(ListingImage).where(
        ListingImage.tenant_id == tenant,
        ListingImage.listing_key == listing,
        ListingImage.pack_id == pack,
    ).order_by(ListingImage.slot)).all()
    if len(rows) < 10:
        return None
    if len(rows) != 10 or [r.slot for r in rows] != list(range(1, 11)):
        raise HTTPException(409, "Incomplete or ambiguous ImageGen pack slots")
    if any(
        r.garment_code != garment or r.design_sha256 != design_sha or
        r.source_sha256 != source_sha or r.placement != placement
        for r in rows
    ):
        raise HTTPException(409, "ImageGen pack source identity changed")
    remote_ids = {r.drive_manifest_id for r in rows if r.drive_manifest_id}
    if len(remote_ids) > 1:
        raise HTTPException(409, "Conflicting ImageGen Drive manifest identities")
    if len(remote_ids) == 1:
        return remote_ids.pop()
    saved = [
        {"slot": r.slot, "output_sha256": r.output_sha256,
         "drive_file_id": r.drive_file_id,
         "native_width": r.native_width, "native_height": r.native_height}
        for r in rows
    ]
    # Do not hold an open read transaction across Google Drive requests.
    db.rollback()
    from .imagegen_drive import store_listing_pack_manifest_to_drive
    remote = await store_listing_pack_manifest_to_drive(
        tenant_id=tenant, listing_id=listing, pack_id=pack,
        garment_code=garment, design_sha256=design_sha,
        source_sha256=source_sha, placement=placement, images=saved,
    )
    db.execute(update(ListingImage).where(
        ListingImage.tenant_id == tenant,
        ListingImage.listing_key == listing,
        ListingImage.pack_id == pack,
        ListingImage.design_sha256 == design_sha,
    ).values(drive_manifest_id=remote))
    db.commit()
    return remote


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
    design_sha = str(payload.get("design_sha256") or "")
    source_sha = str(payload.get("source_sha256") or "")
    placement = str(payload.get("placement") or "")
    if (not _SHA.fullmatch(sha) or not _SHA.fullmatch(design_sha) or
        not _SHA.fullmatch(source_sha) or not _PLACEMENT.fullmatch(placement) or
        payload.get("drive_root_folder_id") != _DRIVE_ROOT):
        raise HTTPException(422, "SHA-bound design and Drive root identity required")
    if db.get(Picked, (agent.tenant_id, listing)) is None:
        raise HTTPException(409, "Listing is not currently Picked")
    key = (agent.tenant_id, listing, pack, slot)
    previous = db.get(ListingImage, key)
    if previous is not None:
        if (previous.output_sha256 != sha or previous.garment_code != garment or
            previous.design_sha256 != design_sha or
            previous.source_sha256 != source_sha or previous.placement != placement):
            raise HTTPException(409, "Immutable ImageGen output slot changed")
        manifest = await _ensure_pack_manifest(
            db, agent.tenant_id, listing, pack, garment,
            design_sha, source_sha, placement)
        return {"ok": True, "already_saved": True, "listing_id": listing,
                "garment_code": garment, "pack_id": pack, "slot": slot,
                "sha256": sha, "design_sha256": design_sha,
                "placement": placement, "storage_root_folder_id": _DRIVE_ROOT,
                "drive_manifest_id": manifest}
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
        tenant_id=agent.tenant_id, pack_id=pack, slot=slot,
        digest=sha, png=png, listing_id=listing,
        design_sha256=design_sha, placement=placement,
        garment_code=garment)
    # Recheck Picked after I/O to prevent a late unpick from authorizing
    # a new visible gallery entry. Re-pick can restore its previous history.
    if db.get(Picked, (agent.tenant_id, listing)) is None:
        raise HTTPException(409, "Listing was unpicked during upload")
    row = ListingImage(
        tenant_id=agent.tenant_id, listing_key=listing, pack_id=pack, slot=slot,
        garment_code=garment, design_sha256=design_sha,
        source_sha256=source_sha, placement=placement,
        drive_folder_id=remote["drive_folder_id"],
        drive_thumbnail_folder_id=remote["drive_thumbnail_folder_id"],
        output_sha256=sha,
        thumbnail_sha256=remote["thumbnail_sha256"],
        drive_file_id=remote["drive_file_id"],
        drive_thumbnail_id=remote["drive_thumbnail_id"],
        native_width=width, native_height=height,
        source_job_id=source_job or None)
    db.add(row)
    db.commit()
    manifest = await _ensure_pack_manifest(
        db, agent.tenant_id, listing, pack, garment,
        design_sha, source_sha, placement)
    return {"ok": True, "already_saved": False, "listing_id": listing,
            "garment_code": garment, "pack_id": pack, "slot": slot,
            "sha256": sha, "design_sha256": design_sha,
            "placement": placement, "storage_root_folder_id": _DRIVE_ROOT,
            "drive_manifest_id": manifest}


def listing_badges(db: Session, tenant_id: str, listing_keys: set[str]):
    """One batched query: per-garment counts plus real C1717 Main thumbnail."""
    if not listing_keys:
        return {}
    counts = defaultdict(dict)
    main_pack = func.min(case(
        (ListingImage.slot == 1, ListingImage.pack_id), else_=None,
    )).label("main_pack")
    rows = db.execute(select(
        ListingImage.listing_key, ListingImage.garment_code,
        func.count(), main_pack,
    ).where(ListingImage.tenant_id == tenant_id,
            ListingImage.listing_key.in_(listing_keys))
      .group_by(ListingImage.listing_key, ListingImage.garment_code))
    for listing_key, garment, quantity, first_main in rows:
        badge = {"garmentCode": garment, "count": int(quantity)}
        if first_main:
            badge["mainThumbnailUrl"] = (
                f"/api/v1/etsy-spy/listing-images/{first_main}/1/thumbnail"
            )
            badge["mainPackId"] = first_main
        counts[listing_key][garment] = badge
    return {key: [group[garment] for garment in sorted(
                group, key=lambda code: (code != "C1717", code))]
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
            "designSha256": row.design_sha256, "placement": row.placement,
            "manifestReady": bool(row.drive_manifest_id),
            "width": row.native_width, "height": row.native_height,
            "savedAt": row.created_at,
            "thumbnailUrl": f"/api/v1/etsy-spy/listing-images/{row.pack_id}/{row.slot}/thumbnail",
            "originalUrl": f"/api/v1/etsy-spy/listing-images/{row.pack_id}/{row.slot}/original",
        }
        groups[row.garment_code].append(image)
    return {"listingKey": listing_key, "total": len(rows),
            "groups": [{"garmentCode": name, "count": len(images), "images": images}
                       for name, images in sorted(groups.items(),
                           key=lambda item: (item[0] != "C1717", item[0]))]}


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
