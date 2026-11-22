"""V3.3 two independent original hero source images per aspect.

Real bytes, not worker-claimed hashes, are validated and stored in the same
private CAM Drive video folder. No local master or new database table.
Visual face/print/content approval remains an independent QA gate.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import hashlib
import re

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from app.domain.providers.contracts import StoreAssetInput, StorageProviderError
from app.modules.etsy_spy.imagegen_drive import (
    provider_and_folder, verify_private_drive_destination,
)
from app.modules.etsy_spy.listing_drive_folders import (
    ROOT_FOLDER_ID, ensure_listing_video_folder, verify_listing_file_provenance,
)
from .video_storage import _VIDEO_LIMIT

MAX_HERO_BYTES = 12 * 1024 * 1024
HERO_RATIOS = {"vertical": "9:16", "horizontal": "16:9"}
HERO_MIME_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG"}
_SHA = re.compile(r"^[a-f0-9]{64}$")
_LISTING = re.compile(r"^[0-9]{5,20}$")


def validate_hero_image(data: bytes, aspect: str, content_type: str) -> dict:
    """Check independently generated portrait/wide image bytes and fingerprint.

    This cannot prove that an AI generator used a distinct prompt, nor
    determine whether a human face is actually visible: human/vision QA must
    inspect both source images and final videos independently.
    """
    if (aspect not in HERO_RATIOS or content_type not in HERO_MIME_FORMATS
            or len(data) < 2048 or len(data) > MAX_HERO_BYTES):
        raise HTTPException(422, "Invalid or oversized hero source image")
    try:
        with Image.open(BytesIO(data)) as img:
            width, height = ImageOps.exif_transpose(img).size
            if (not 640 <= width <= 4096 or not 640 <= height <= 4096
                    or width * height > 12_000_000
                    or img.format != HERO_MIME_FORMATS[content_type]
                    or getattr(img, "n_frames", 1) != 1):
                raise ValueError("Unsupported dimensions, format, or animated image")
            img.load()
            source = ImageOps.exif_transpose(img).convert("RGB")
            normalized = source.resize((64, 64), Image.Resampling.LANCZOS)
            digest_pixels = hashlib.sha256(normalized.tobytes()).hexdigest()
            gray = source.resize((9, 8), Image.Resampling.LANCZOS).convert("L")
            pix = list(gray.getdata())
            phash = sum(
                (1 << (row * 8 + col))
                for row in range(8) for col in range(8)
                if pix[row * 9 + col] > pix[row * 9 + col + 1]
            )
    except (OSError, ValueError, TypeError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Hero image could not be decoded safely") from exc
    expected = (9 / 16) if aspect == "vertical" else (16 / 9)
    if abs(width / height - expected) > 0.09:
        raise HTTPException(422, "Hero must be generated natively for its aspect ratio")
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "pixelsSha256": digest_pixels,
        "dHash64": f"{phash:016x}",
        "width": width,
        "height": height,
        "contentType": content_type,
        "aspectRatio": HERO_RATIOS[aspect],
        "bytes": len(data),
    }


def heroes_are_distinct(left: dict, right: dict) -> bool:
    """Enforce distinct bytes, decoded pixels, and obvious visual content."""
    if (left.get("sha256") == right.get("sha256")
            or left.get("pixelsSha256") == right.get("pixelsSha256")
            or left.get("heroRequestId") == right.get("heroRequestId")
            or left.get("heroSeed") == right.get("heroSeed")):
        return False
    try:
        distance = (
            int(left["dHash64"], 16) ^ int(right["dHash64"], 16)
        ).bit_count()
    except (KeyError, TypeError, ValueError):
        return False
    return distance > 5


async def store_muse_hero(
    *, tenant_id: str, listing_id: str, artwork_sha256: str,
    aspect: str, image_bytes: bytes, metadata: dict,
) -> dict:
    if (not _LISTING.fullmatch(listing_id) or not _SHA.fullmatch(artwork_sha256)
            or aspect not in HERO_RATIOS or
            hashlib.sha256(image_bytes).hexdigest() != metadata.get("sha256")):
        raise HTTPException(422, "Hero provenance does not match CAM job")
    content_type = metadata["contentType"]
    ext = ".jpg" if content_type == "image/jpeg" else ".png"
    async with _VIDEO_LIMIT:
        provider, root = provider_and_folder()
        if root != ROOT_FOLDER_ID:
            raise HTTPException(503, "CAM hero Drive root differs from ImageGen root")
        await verify_private_drive_destination(provider, root)
        folders = await ensure_listing_video_folder(
            provider=provider, root_folder_id=root, tenant_id=tenant_id,
            listing_id=listing_id, design_sha256=artwork_sha256,
        )
        asset_id = f"muse-hero:{listing_id}:{artwork_sha256}:{aspect}"
        filename = f"ugc-hero-{aspect}{ext}"

        async def chunks():
            for offset in range(0, len(image_bytes), 1024 * 1024):
                yield image_bytes[offset:offset + 1024 * 1024]

        try:
            stored = await provider.store_asset(StoreAssetInput(
                tenant_id=tenant_id, asset_id=asset_id,
                content_hash=metadata["sha256"],
                destination_folder_id=folders["video_folder_id"],
                filename=filename, content_type=content_type,
                size_bytes=len(image_bytes), body=chunks(),
            ))
        except StorageProviderError as exc:
            raise HTTPException(
                503 if exc.retryable else 502, "Private CAM hero Drive upload failed"
            ) from exc
        remote_id = str(stored.remote_file_id or "")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", remote_id)
                or stored.remote_folder_id != folders["video_folder_id"]):
            raise HTTPException(502, "Hero saved outside approved listing folder")
        await verify_listing_file_provenance(
            provider=provider, remote_file_id=remote_id,
            folder_id=folders["video_folder_id"], tenant_id=tenant_id,
            asset_id=asset_id, sha256=metadata["sha256"],
        )
    return {
        **metadata,
        "drive_file_id": remote_id,
        "drive_folder_id": folders["video_folder_id"],
        "listingFolderId": folders["listing_folder_id"],
        "relativeFolder": folders["relative_path"],
        "storage_provider": "google_drive_managed",
        "storageVerified": True,
        "faceVisibilityQa": "pending_review",
        "heroDifferenceQa": "pending_review",
    }
