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

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func, case, update, or_
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
_SUPPORTED_SLOT_COUNTS = frozenset({10, 13})


def listing_preview_requires_review(width: int | None, height: int | None) -> bool:
    """Native output below 2000px is an internal preview, not final listing QA."""
    return width is None or height is None or width < 2000 or height < 2000


def verified_identity(listing_key: str, pack_id: str, slot: int,
                      garment: str | None = None, *, expected_slots: int = 10):
    if (expected_slots not in _SUPPORTED_SLOT_COUNTS or
        not _LISTING.fullmatch(str(listing_key)) or
        not _PACK.fullmatch(str(pack_id)) or
        not isinstance(slot, int) or isinstance(slot, bool) or
        not 1 <= slot <= expected_slots):
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
    *, main_revision: bool = False, expected_slots: int = 10,
) -> str | None:
    """Manifest requires all ten legacy or thirteen V13 saved slots.

    Retries, including a lost ACK after the last upload, recreate neither
    images nor a manifest; they only reconcile the original immutable pack.
    """
    rows = db.scalars(select(ListingImage).where(
        ListingImage.tenant_id == tenant,
        ListingImage.listing_key == listing,
        ListingImage.pack_id == pack,
    ).order_by(ListingImage.slot)).all()
    if len(rows) < expected_slots:
        return None
    if len(rows) != expected_slots or [r.slot for r in rows] != list(range(1, expected_slots + 1)):
        raise HTTPException(409, "Incomplete or ambiguous ImageGen pack slots")
    if any(
        r.garment_code != garment or r.design_sha256 != design_sha or
        r.source_sha256 != source_sha or r.placement != placement
        for r in rows
    ):
        raise HTTPException(409, "ImageGen pack source identity changed")
    # Completed 10-role generation is not equivalent to a listing-approved pack.
    # Save individual native previews for internal review, but do not write a
    # final Drive pack manifest if any role remains below native 2000px.
    if any(listing_preview_requires_review(r.native_width, r.native_height)
           for r in rows):
        return None
    remote_ids = {r.drive_manifest_id for r in rows if r.drive_manifest_id}
    if len(remote_ids) > 1:
        raise HTTPException(409, "Conflicting ImageGen Drive manifest identities")
    if len(remote_ids) == 1:
        return remote_ids.pop()
    folder_ids = {r.drive_folder_id for r in rows}
    if len(folder_ids) != 1:
        raise HTTPException(409, "Pack PNGs are scattered across multiple Drive folders")
    expected_folder_id = folder_ids.pop()
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
        expected_drive_folder_id=expected_folder_id,
        expected_slots=expected_slots,
        main_sha256=rows[0].output_sha256 if main_revision else None,
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
    expected_slots = payload.get("expected_slots", 10)
    if type(expected_slots) is not int or expected_slots not in _SUPPORTED_SLOT_COUNTS:
        raise HTTPException(422, "Expected 10 legacy or 13 V13 listing slots")
    verified_identity(listing, pack, slot, garment, expected_slots=expected_slots)
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
    # Main-only revision is explicit compare-and-swap, never a normal resend.
    replace_sha = payload.get("replace_slot_sha256")
    revision_number = payload.get("main_revision_number")
    if replace_sha is not None:
        if (slot != 1 or garment != "C1717" or
            not isinstance(replace_sha, str) or not _SHA.fullmatch(replace_sha) or
            replace_sha == sha or not isinstance(revision_number, int) or
            isinstance(revision_number, bool) or
            not 1 <= revision_number <= 10000):
            raise HTTPException(422, "Invalid C1717 Main replacement receipt")
    elif revision_number is not None:
        raise HTTPException(422, "Main revision number requires previous SHA")
    key = (agent.tenant_id, listing, pack, slot)
    previous = db.get(ListingImage, key)
    if previous is not None:
        if (previous.garment_code != garment or
            previous.design_sha256 != design_sha or
            previous.source_sha256 != source_sha or previous.placement != placement):
            raise HTTPException(409, "Immutable ImageGen pack source changed")
        if previous.output_sha256 != sha:
            if replace_sha is None or previous.output_sha256 != replace_sha:
                raise HTTPException(409, "Immutable ImageGen output slot changed")
        else:
            manifest = await _ensure_pack_manifest(
                db, agent.tenant_id, listing, pack, garment,
                design_sha, source_sha, placement,
                main_revision=bool(replace_sha), expected_slots=expected_slots)
            return {"ok": True, "already_saved": True, "listing_id": listing,
                    "garment_code": garment, "pack_id": pack, "slot": slot,
                    "sha256": sha, "design_sha256": design_sha,
                    "placement": placement, "storage_root_folder_id": _DRIVE_ROOT,
                    "preview_only": listing_preview_requires_review(
                        previous.native_width, previous.native_height),
                    "drive_manifest_id": manifest}
    elif replace_sha is not None:
        raise HTTPException(409, "Main replacement requires existing slot")
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
    review_only = listing_preview_requires_review(width, height)
    declared_review = payload.get("preview_only")
    if declared_review is not None and type(declared_review) is not bool:
        raise HTTPException(422, "Invalid Listing Image preview-only flag")
    if declared_review is True and not review_only:
        raise HTTPException(422, "Preview-only PNG must retain actual native dimensions")
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
        garment_code=garment, expected_slots=expected_slots,
        **({"main_revision_number": revision_number} if replace_sha else {}))
    # Recheck Picked after I/O to prevent a late unpick from authorizing
    # a new visible gallery entry. Re-pick can restore its previous history.
    if db.get(Picked, (agent.tenant_id, listing)) is None:
        raise HTTPException(409, "Listing was unpicked during upload")
    if replace_sha:
        changed = db.execute(update(ListingImage).where(
            ListingImage.tenant_id == agent.tenant_id,
            ListingImage.listing_key == listing,
            ListingImage.pack_id == pack,
            ListingImage.slot == 1,
            ListingImage.output_sha256 == replace_sha,
        ).values(
            output_sha256=sha,
            thumbnail_sha256=remote["thumbnail_sha256"],
            drive_file_id=remote["drive_file_id"],
            drive_thumbnail_id=remote["drive_thumbnail_id"],
            drive_folder_id=remote["drive_folder_id"],
            drive_thumbnail_folder_id=remote["drive_thumbnail_folder_id"],
            native_width=width, native_height=height,
            source_job_id=source_job or None,
        ))
        if changed.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "Main changed while uploading replacement")
        # A new 10-role Drive manifest is written without altering 02-10.
        db.execute(update(ListingImage).where(
            ListingImage.tenant_id == agent.tenant_id,
            ListingImage.listing_key == listing,
            ListingImage.pack_id == pack,
        ).values(drive_manifest_id=None))
    else:
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
        design_sha, source_sha, placement,
        main_revision=bool(replace_sha), expected_slots=expected_slots)
    return {"ok": True, "already_saved": False,
            "main_replaced": bool(replace_sha), "listing_id": listing,
            "garment_code": garment, "pack_id": pack, "slot": slot,
            "sha256": sha, "design_sha256": design_sha,
            "placement": placement, "storage_root_folder_id": _DRIVE_ROOT,
            "preview_only": review_only,
            "drive_manifest_id": manifest}


@listing_image_router.get("/image-agents/{agent_id}/listing-image-coverage")
def read_agent_listing_image_coverage(
    agent_id: str, authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Read-only, tenant-scoped proof of which Picked listings already have an image.

    The Image Agent needs this before creating new Skill jobs. No URLs, Drive
    identifiers or other tenants' listings are returned. Fail closed on an
    unavailable endpoint rather than generating duplicate listing art.
    """
    agent = authenticate_agent(db, agent_id, authorization, image_only=True)
    keys = db.scalars(select(ListingImage.listing_key).join(
        Picked, (Picked.tenant_id == ListingImage.tenant_id) &
                (Picked.listing_key == ListingImage.listing_key)
    ).where(ListingImage.tenant_id == agent.tenant_id).distinct().limit(10001)).all()
    if len(keys) > 10000:
        raise HTTPException(409, "Listing Image coverage exceeds safe bound")
    return {"protocol": "cam-picked-listing-image-coverage-v1",
            "complete": True, "coveredListingIds": sorted(set(keys)),
            "total": len(set(keys))}


def listing_badges(db: Session, tenant_id: str, listing_keys: set[str]):
    """One batched query: per-garment counts plus real C1717 Main thumbnail."""
    if not listing_keys:
        return {}
    counts = defaultdict(dict)
    # Two bounded page-level queries avoid N+1 and show newest saved Main.
    review_case = case((or_(
        ListingImage.native_width.is_(None),
        ListingImage.native_height.is_(None),
        ListingImage.native_width < 2000,
        ListingImage.native_height < 2000,
    ), 1), else_=0)
    rows = db.execute(select(
        ListingImage.listing_key, ListingImage.garment_code,
        func.count(), func.sum(review_case),
    ).where(ListingImage.tenant_id == tenant_id,
            ListingImage.listing_key.in_(listing_keys))
      .group_by(ListingImage.listing_key, ListingImage.garment_code))
    for listing_key, garment, quantity, review_quantity in rows:
        counts[listing_key][garment] = {
            "garmentCode": garment, "count": int(quantity),
            "reviewCount": int(review_quantity or 0)}
    # The row thumbnail must represent any saved ImageGen output, even when
    # slot 01 (Main) has not been uploaded yet. Previously only slot 01 was
    # queried, leaving the Picked primary cell blank despite saved slots 02+.
    # Keep the latest real Main separately for callers which require slot 01.
    saved = db.execute(select(
        ListingImage.listing_key, ListingImage.garment_code,
        ListingImage.pack_id, ListingImage.slot, ListingImage.thumbnail_sha256,
    ).where(ListingImage.tenant_id == tenant_id,
            ListingImage.listing_key.in_(listing_keys))
      .order_by(ListingImage.created_at.desc(), ListingImage.pack_id.desc(),
                ListingImage.slot.asc()))
    for listing_key, garment, pack_id, slot, thumbnail_sha in saved:
        badge = counts.get(listing_key, {}).get(garment)
        if not badge:
            continue
        if (not _PACK.fullmatch(pack_id) or not _SHA.fullmatch(thumbnail_sha)
                or not isinstance(slot, int) or not 1 <= slot <= 13):
            raise HTTPException(409, "Invalid saved Listing Image preview identity")
        url = (f"/api/v1/etsy-spy/listing-images/{pack_id}/{slot}/thumbnail"
               f"?v={thumbnail_sha[:12]}")
        if "previewThumbnailUrl" not in badge:
            badge["previewThumbnailUrl"] = url
            badge["previewPackId"] = pack_id
            badge["previewSlot"] = slot
        if slot == 1 and "mainThumbnailUrl" not in badge:
            badge["mainThumbnailUrl"] = url
            badge["mainPackId"] = pack_id
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
            "manifestReady": bool(row.drive_manifest_id) and not
                listing_preview_requires_review(row.native_width, row.native_height),
            "previewOnly": listing_preview_requires_review(
                row.native_width, row.native_height),
            "width": row.native_width, "height": row.native_height,
            "savedAt": row.created_at,
            "thumbnailUrl": f"/api/v1/etsy-spy/listing-images/{row.pack_id}/{row.slot}/thumbnail?v={row.thumbnail_sha256[:12]}",
            "previewUrl": f"/api/v1/etsy-spy/listing-images/{row.pack_id}/{row.slot}/preview?v={row.output_sha256[:12]}-w720v1",
            "originalUrl": f"/api/v1/etsy-spy/listing-images/{row.pack_id}/{row.slot}/original?v={row.output_sha256[:12]}",
        }
        groups[row.garment_code].append(image)
    return {"listingKey": listing_key, "total": len(rows),
            "groups": [{"garmentCode": name, "count": len(images), "images": images}
                       for name, images in sorted(groups.items(),
                           key=lambda item: (item[0] != "C1717", item[0]))]}


def _listing_image_matches_etag(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    return any(
        tag.strip() in (etag, "W/" + etag, "*")
        for tag in if_none_match.split(",")[:32]
    )


@listing_image_router.get("/listing-images/{pack_id}/{slot}/{quality}")
async def read_listing_output_image(
    pack_id: str, slot: int, quality: str, background_tasks: BackgroundTasks,
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None),
):
    if (not _PACK.fullmatch(pack_id) or not 1 <= slot <= 13
            or quality not in {"thumbnail", "preview", "original"}):
        raise HTTPException(404, "ImageGen listing output not found")
    tenant = principal.active_tenant_id
    # IMPORTANT: check the principal, tenant and current Picked ACL BEFORE
    # a 304/cache response, so cross-tenant or unpicked objects never validate.
    row = db.scalar(select(ListingImage).where(
        ListingImage.tenant_id == tenant, ListingImage.pack_id == pack_id,
        ListingImage.slot == slot,
        select(Picked.listing_key).where(
            Picked.tenant_id == tenant,
            Picked.listing_key == ListingImage.listing_key).exists(),
    ))
    if row is None:
        raise HTTPException(404, "ImageGen listing output not found")
    from .listing_image_preview import PREVIEW_VERSION
    sha = row.thumbnail_sha256 if quality == "thumbnail" else row.output_sha256
    fingerprint = hashlib.sha256(
        (sha + ":" + (PREVIEW_VERSION if quality == "preview" else quality)).encode("ascii")
    ).hexdigest()
    etag = '"' + fingerprint + '"'
    headers = {
        "Cache-Control": "private, no-cache, must-revalidate",
        "Vary": "Cookie, Authorization",
        "X-Content-Type-Options": "nosniff",
        "ETag": etag,
    }
    if _listing_image_matches_etag(if_none_match, etag):
        return Response(status_code=304, headers=headers)

    drive_id = row.drive_thumbnail_id if quality == "thumbnail" else row.drive_file_id
    # CDN mode is completely opt-in and never applies to original PNGs.
    # Every ticket is issued only AFTER the same principal/tenant/Picked ACL.
    from .listing_image_cdn import cdn_config, available_cdn_url, stage_derivative
    cdn = cdn_config(tenant_id=tenant) if quality in {"thumbnail", "preview"} else None
    from .listing_image_shared_r2 import (
        config_for_tenant as shared_r2_config,
        read_derivative as read_shared_derivative,
        object_key as shared_object_key,
        signed_worker_url as shared_worker_url,
        worker_ready as shared_worker_ready,
        stage_derivative as stage_shared_derivative,
    )
    shared = (shared_r2_config(tenant)
              if quality in {"thumbnail", "preview"} and cdn is None else None)
    if cdn is not None:
        # Release the SELECT transaction before even a fast Worker HEAD probe.
        db.rollback()
        cdn_url = await available_cdn_url(
            cdn, tenant_id=tenant, pack_id=pack_id,
            slot=slot, quality=quality, sha256=sha)
        if cdn_url:
            return RedirectResponse(
                cdn_url, status_code=302,
                headers={"Cache-Control": "private, no-store",
                         "Vary": "Cookie, Authorization",
                         "Referrer-Policy": "no-referrer",
                         "X-Content-Type-Options": "nosniff"})

    # Release SELECT transaction before private R2/Drive network I/O.
    db.rollback()
    if shared is not None:
        # Reuse CAM's EXISTING private R2 bucket via its own image namespace.
        # The currently deployed video Worker is video-only, so edge delivery
        # remains a separate gate until signed image routes are verified.
        cached = await read_shared_derivative(
            shared, tenant_id=tenant, pack_id=pack_id, slot=slot,
            quality=quality, sha256=sha)
        if cached is not None:
            signed = (shared_worker_url(
                shared, shared_object_key(
                    shared, tenant_id=tenant, pack_id=pack_id,
                    slot=slot, quality=quality, sha256=sha))
                if await shared_worker_ready(shared) else None)
            if signed:
                return RedirectResponse(signed, status_code=302, headers={
                    "Cache-Control": "private, no-store",
                    "Vary": "Cookie, Authorization",
                    "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                })
            return Response(
                cached,
                media_type="image/webp" if quality == "preview" else "image/png",
                headers={**headers, "X-CAM-Image-Cache": "R2-HIT"})
    if quality == "preview":
        from .listing_image_preview import get_listing_preview
        webp = await get_listing_preview(
            tenant_id=tenant, pack_id=pack_id, slot=slot,
            remote_id=drive_id, sha256=sha)
        if cdn is not None:
            background_tasks.add_task(
                stage_derivative, cdn, tenant_id=tenant, pack_id=pack_id,
                slot=slot, quality=quality, sha256=sha, data=webp)
        if shared is not None:
            background_tasks.add_task(
                stage_shared_derivative, shared, tenant_id=tenant, pack_id=pack_id,
                slot=slot, quality=quality, sha256=sha, data=webp)
        return Response(webp, media_type="image/webp", headers=headers)
    from .imagegen_drive import read_design_from_drive
    png = await read_design_from_drive(
        tenant_id=tenant, asset_id=f"{pack_id}:listing-{slot:02d}:{quality}",
        remote_id=drive_id, sha256=sha,
        max_bytes=2 * 1024 * 1024 if quality == "thumbnail" else _MAX_PNG)
    if cdn is not None:
        background_tasks.add_task(
            stage_derivative, cdn, tenant_id=tenant, pack_id=pack_id,
            slot=slot, quality=quality, sha256=sha, data=png)
    if shared is not None:
        background_tasks.add_task(
            stage_shared_derivative, shared, tenant_id=tenant, pack_id=pack_id,
            slot=slot, quality=quality, sha256=sha, data=png)
    return Response(png, media_type="image/png", headers=headers)