"""Muse video output: bounded MP4 validation and private listing-scoped Drive upload.

No rendering model is invoked. Each MP4 must be supplied by the authorized
Muse worker and is stored in the existing CAM ImageGen Drive hierarchy.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from .native_mp4 import inspect_native_mp4, InvalidNativeMP4, MIN_VIDEO_BYTES, MAX_VIDEO_BYTES
from .video_asset_identity import video_asset_id

from fastapi import HTTPException

from app.domain.providers.contracts import StoreAssetInput, StorageProviderError
from app.modules.etsy_spy.imagegen_drive import (
    provider_and_folder, verify_private_drive_destination,
)
from app.modules.etsy_spy.listing_drive_folders import (
    ROOT_FOLDER_ID, ensure_listing_video_folder, verify_listing_file_provenance,
)

_VIDEO_LIMIT = asyncio.Semaphore(1)
_SHA = re.compile(r"^[a-f0-9]{64}$")
_LISTING = re.compile(r"^[0-9]{5,20}$")
_RATIOS = {"vertical": "9:16", "horizontal": "16:9"}


def validate_mp4(mp4: bytes, aspect: str) -> dict:
    """Check container and read optional header metadata; never decode MP4."""
    if aspect not in _RATIOS:
        raise HTTPException(422, "Invalid video aspect")
    try:
        metadata = inspect_native_mp4(mp4)
    except InvalidNativeMP4 as exc:
        raise HTTPException(422, "Invalid or incomplete MP4 container") from exc
    # Aspect is supplied by the claimed ratio. If the existing MP4 header
    # exposes display dimensions, reject an obvious wrong-ratio delivery.
    width, height = metadata.get("width"), metadata.get("height")
    target = 9 / 16 if aspect == "vertical" else 16 / 9
    if width and height and abs(width / height - target) > 0.07:
        raise HTTPException(422, "Native MP4 aspect does not match claim")
    return metadata


async def store_muse_video(
    *, tenant_id: str, listing_id: str, artwork_sha256: str,
    aspect: str, mp4: bytes,
    required_duration_seconds: tuple[float, float] | None = None,
    storage_revision: str | None = None,
) -> dict:
    """No disk/master copy on VPS. Drive only, with full provenance readback."""
    if (not _LISTING.fullmatch(listing_id)
            or not _SHA.fullmatch(artwork_sha256)
            or aspect not in _RATIOS):
        raise HTTPException(422, "Invalid listing artwork or video aspect")
    metadata = validate_mp4(mp4, aspect)
    if required_duration_seconds is not None:
        minimum, maximum = required_duration_seconds
        duration = metadata.get("durationSeconds")
        if duration is None or not minimum <= duration <= maximum:
            raise HTTPException(
                422, "Story export must match its approved final duration; no loops or padding"
            )
    digest = hashlib.sha256(mp4).hexdigest()
    async with _VIDEO_LIMIT:
        provider, root = provider_and_folder()
        if root != ROOT_FOLDER_ID:
            raise HTTPException(503, "CAM video output Drive folder does not match ImageGen root")
        await verify_private_drive_destination(provider, root)
        folders = await ensure_listing_video_folder(
            provider=provider, root_folder_id=root, tenant_id=tenant_id,
            listing_id=listing_id, design_sha256=artwork_sha256,
        )
        try:
            asset_id = video_asset_id(listing_id, artwork_sha256, aspect, storage_revision)
        except ValueError as exc:
            raise HTTPException(422, "Invalid UGC storage revision") from exc
        filename = f"ugc-{aspect}.mp4" if storage_revision is None else f"ugc-{aspect}-{storage_revision}.mp4"
        async def chunks():
            for offset in range(0, len(mp4), 1024 * 1024):
                yield mp4[offset: offset + 1024 * 1024]
        try:
            stored = await provider.store_asset(StoreAssetInput(
                tenant_id=tenant_id, asset_id=asset_id, content_hash=digest,
                destination_folder_id=folders["video_folder_id"],
                filename=filename, content_type="video/mp4",
                size_bytes=len(mp4), body=chunks(),
            ))
        except StorageProviderError as exc:
            raise HTTPException(
                503 if exc.retryable else 502, "CAM Drive video upload failed"
            ) from exc
        remote_id = str(stored.remote_file_id or "")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", remote_id)
                or stored.remote_folder_id != folders["video_folder_id"]):
            raise HTTPException(502, "Drive did not return correct video folder")
        await verify_listing_file_provenance(
            provider=provider, remote_file_id=remote_id,
            folder_id=folders["video_folder_id"],
            tenant_id=tenant_id, asset_id=asset_id, sha256=digest,
        )
    # Drive remains the canonical source. Stage only once, after the verified
    # upload; browsing never refreshes R2's fixed 30-day lifecycle.
    # This is an optional acceleration layer, so a cache miss does not reject
    # a successful Muse upload. The input bytes are already bounded to 40 MiB.
    try:
        from .ugc_media_cache import config_for_tenant, stage_uploaded
        await asyncio.wait_for(
            stage_uploaded(config_for_tenant(tenant_id),
                           tenant=tenant_id, sha=digest, mp4=mp4),
            timeout=45,
        )
    except Exception:
        pass  # Keep the verified Drive receipt and normal playback fallback.
    return {
        "aspectRatio": _RATIOS[aspect],
        "sha256": digest, "bytes": len(mp4),
        "contentType": "video/mp4", "storage_provider": "google_drive_managed",
        "drive_file_id": remote_id,
        "drive_folder_id": folders["video_folder_id"],
        "listingFolderId": folders["listing_folder_id"],
        "relativeFolder": folders["relative_path"],
        "storageVerified": True, "qaStatus": "pending_review",
        "mediaProcessing": "none", "nativeBytesPreserved": True,
        **({"storageRevision": storage_revision} if storage_revision is not None else {}),
        **metadata,
    }