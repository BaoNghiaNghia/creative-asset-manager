"""Muse video output: bounded MP4 validation and private listing-scoped Drive upload.

No rendering model is invoked. Each MP4 must be supplied by the authorized
Muse worker and is stored in the existing CAM ImageGen Drive hierarchy.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess

from fastapi import HTTPException

from app.domain.providers.contracts import StoreAssetInput, StorageProviderError
from app.modules.etsy_spy.imagegen_drive import (
    provider_and_folder, verify_private_drive_destination,
)
from app.modules.etsy_spy.listing_drive_folders import (
    ROOT_FOLDER_ID, ensure_listing_video_folder, verify_listing_file_provenance,
)

MAX_VIDEO_BYTES = 40 * 1024 * 1024
MIN_VIDEO_BYTES = 2048
_VIDEO_LIMIT = asyncio.Semaphore(1)
_SHA = re.compile(r"^[a-f0-9]{64}$")
_LISTING = re.compile(r"^[0-9]{5,20}$")
_RATIOS = {"vertical": "9:16", "horizontal": "16:9"}


def validate_mp4(mp4: bytes, aspect: str) -> dict:
    """Reject corrupted, wrong-ratio, non-H264, or non-video payloads."""
    if (aspect not in _RATIOS or not MIN_VIDEO_BYTES <= len(mp4) <= MAX_VIDEO_BYTES
            or mp4[4:8] != b"ftyp" or b"moov" not in mp4
            or b"mdat" not in mp4):
        raise HTTPException(422, "Invalid or incomplete MP4")
    try:
        checked = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,codec_type,width,height,duration",
             "-show_entries", "format=duration",
             "-of", "json", "-i", "pipe:0"],
            input=mp4, capture_output=True, timeout=15, check=True,
        )
        probe = json.loads(checked.stdout[:60000])
        visual = next(
            v for v in probe.get("streams", [])
            if v.get("codec_type") == "video"
        )
        width = int(visual["width"])
        height = int(visual["height"])
        duration = float(probe.get("format", {}).get("duration")
                         or visual.get("duration") or 0)
    except (OSError, ValueError, TypeError, KeyError, StopIteration,
            subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "MP4 cannot be decoded by ffprobe") from exc
    target = 9 / 16 if aspect == "vertical" else 16 / 9
    if (visual.get("codec_name") != "h264"
            or not 256 <= width <= 4096 or not 256 <= height <= 4096
            or abs(width / height - target) > 0.07
            or not 5 <= duration <= 60):
        raise HTTPException(422, "Video codec, aspect ratio or duration is invalid")
    return {"width": width, "height": height, "durationSeconds": round(duration, 2)}


async def store_muse_video(
    *, tenant_id: str, listing_id: str, artwork_sha256: str,
    aspect: str, mp4: bytes,
    required_duration_seconds: tuple[float, float] | None = None,
) -> dict:
    """No disk/master copy on VPS. Drive only, with full provenance readback."""
    if (not _LISTING.fullmatch(listing_id)
            or not _SHA.fullmatch(artwork_sha256)
            or aspect not in _RATIOS):
        raise HTTPException(422, "Invalid listing artwork or video aspect")
    metadata = await asyncio.to_thread(validate_mp4, mp4, aspect)
    if required_duration_seconds is not None:
        minimum, maximum = required_duration_seconds
        if not minimum <= metadata["durationSeconds"] <= maximum:
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
        asset_id = f"muse-ugc:{listing_id}:{artwork_sha256}:{aspect}"
        filename = "ugc-vertical.mp4" if aspect == "vertical" else "ugc-horizontal.mp4"
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
        **metadata,
    }
