"""Tenant-bound slot coverage from the production metadata snapshot.
No Drive request, no QA inference, no mutation. Preserve legacy semantics.
"""
from sqlalchemy import select, func, literal, union_all, exists, null, cast, String, Integer
from fastapi import HTTPException
import re

from .model import (EtsySpyPicked as Picked, EtsySpyListingImage as ListingImage,
                    EtsySpyListingImageDeletion as DeletedListingImage)

MAX_PICKED=10000
MAX_IMAGES=30000
MAX_DELETIONS=30000
_LISTING=re.compile(r"^[0-9]{5,20}$")
_PACK=re.compile(r"^sp-[0-9a-f]{40}$")
_SHA=re.compile(r"^[0-9a-f]{64}$")
_PLACEMENT=re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_GARMENTS=frozenset({"C1717","G500","G180","G185"})


def listing_preview_requires_review(width, height):
    return width is None or height is None or width < 2000 or height < 2000


def slot_coverage(db, tenant):
    picked = select(Picked.listing_key.label("listing_key")).where(
        Picked.tenant_id == tenant
    ).order_by(Picked.listing_key).limit(MAX_PICKED+1).cte("picked_slot_coverage")
    images = select(
        ListingImage.listing_key.label("listing_key"),
        ListingImage.pack_id.label("pack_id"),
        ListingImage.slot.label("slot"),
        ListingImage.garment_code.label("garment_code"),
        ListingImage.design_sha256.label("design_sha256"),
        ListingImage.source_sha256.label("source_sha256"),
        ListingImage.placement.label("placement"),
        ListingImage.output_sha256.label("sha"),
        ListingImage.native_width.label("native_width"),
        ListingImage.native_height.label("native_height"),
    ).join(picked, picked.c.listing_key == ListingImage.listing_key).where(
        ListingImage.tenant_id == tenant
    ).limit(MAX_IMAGES+1).cte("saved_slot_coverage")
    occupied = exists(select(1).where(
        ListingImage.tenant_id == tenant,
        ListingImage.listing_key == DeletedListingImage.listing_key,
        ListingImage.pack_id == DeletedListingImage.pack_id,
        ListingImage.slot == DeletedListingImage.slot,
    ))
    ranked = select(
        DeletedListingImage.listing_key.label("listing_key"),
        DeletedListingImage.pack_id.label("pack_id"),
        DeletedListingImage.slot.label("slot"),
        DeletedListingImage.output_sha256.label("sha"),
        func.row_number().over(
            partition_by=(DeletedListingImage.listing_key,
                          DeletedListingImage.pack_id, DeletedListingImage.slot),
            order_by=(DeletedListingImage.deleted_at.desc(),
                      DeletedListingImage.output_sha256.desc()),
        ).label("rank"),
    ).join(picked, picked.c.listing_key == DeletedListingImage.listing_key).where(
        DeletedListingImage.tenant_id == tenant, ~occupied
    ).cte("ranked_slot_deletions")
    deletions = select(ranked.c.listing_key, ranked.c.pack_id,
                       ranked.c.slot, ranked.c.sha).where(
        ranked.c.rank == 1
    ).limit(MAX_DELETIONS+1).cte("pending_slot_deletions")
    # PostgreSQL cannot infer two different types for one shared NULL bind.
    # Cast SQL NULL literals (no bind parameter) to the matching UNION columns.
    null_text = cast(null(), String())
    null_int = cast(null(), Integer())
    statement = union_all(
        select(literal("image").label("kind"), images.c.listing_key,
               images.c.pack_id, images.c.slot, images.c.garment_code,
               images.c.design_sha256, images.c.source_sha256,
               images.c.placement, images.c.sha, images.c.native_width,
               images.c.native_height),
        select(literal("deletion"), deletions.c.listing_key, deletions.c.pack_id,
               deletions.c.slot, null_text, null_text, null_text, null_text,
               deletions.c.sha, null_int, null_int),
        select(literal("picked"), picked.c.listing_key, null_text, null_int,
               null_text, null_text, null_text, null_text, null_text, null_int, null_int),
    )
    picked_ids, receipts, pending = [], [], []
    seen_images, seen_deletions = set(), set()
    for row in db.execute(statement):
        if not _LISTING.fullmatch(row.listing_key or ""):
            raise HTTPException(409, "Invalid Picked listing identity in slot coverage")
        if row.kind == "picked":
            picked_ids.append(row.listing_key)
            continue
        if (not _PACK.fullmatch(row.pack_id or "") or
            type(row.slot) is not int or not 1 <= row.slot <= 13 or
            not _SHA.fullmatch(row.sha or "")):
            raise HTTPException(409, "Invalid saved slot identity in coverage")
        key = (row.listing_key, row.pack_id, row.slot)
        identity = {"listing_id": row.listing_key, "pack_id": row.pack_id, "slot": row.slot}
        if row.kind == "image":
            if (key in seen_images or row.garment_code not in _GARMENTS or
                not _SHA.fullmatch(row.design_sha256 or "") or
                not _SHA.fullmatch(row.source_sha256 or "") or
                not _PLACEMENT.fullmatch(row.placement or "")):
                raise HTTPException(409, "Invalid ImageGen upload receipt in slot coverage")
            seen_images.add(key)
            receipts.append({
                **identity, "garment_code": row.garment_code,
                "design_sha256": row.design_sha256,
                "source_sha256": row.source_sha256,
                "placement": row.placement, "sha256": row.sha,
                "preview_only": listing_preview_requires_review(
                    row.native_width, row.native_height),
            })
        else:
            if key in seen_deletions:
                raise HTTPException(409, "Duplicate deletion receipt in slot coverage")
            seen_deletions.add(key)
            pending.append({**identity, "deleted_sha256": row.sha})
    picked_set = set(picked_ids)
    if (len(picked_ids) != len(picked_set) or len(picked_ids) > MAX_PICKED or
        len(receipts) > MAX_IMAGES or len(pending) > MAX_DELETIONS or
        seen_images & seen_deletions or
        any(key[0] not in picked_set for key in seen_images | seen_deletions)):
        raise HTTPException(409, "Listing Image slot coverage exceeds safe bounds or is inconsistent")
    sort_key = lambda row: (row["listing_id"], row["pack_id"], row["slot"])
    return {
        "protocol": "cam-picked-listing-slot-coverage-v1", "complete": True,
        "storage_evidence": "cam_upload_receipts", "source_sha256_included": True,
        "qa_verified": False, "drive_reverified": False,
        "picked_listing_ids": sorted(picked_ids), "picked_total": len(picked_ids),
        "images": sorted(receipts, key=sort_key), "image_total": len(receipts),
        "pending_deletions": sorted(pending, key=sort_key), "deletion_total": len(pending),
    }
