"""Private Google Drive storage for Etsy Picked Design PNGs.

The VPS never persists new mapped Design image bytes. Uploads are memory-only,
idempotent by immutable CAM job/asset/SHA, and fail closed when Drive OAuth
is not configured. Existing historical local assets remain readable.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from typing import AsyncIterator

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.domain.providers.contracts import OpenStoredAssetInput, StoreAssetInput, StorageProviderError
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.google.storage import GoogleDriveAssetStorage

_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_MAX_PNG = 80 * 1024 * 1024


def provider_and_folder():
    """Use CAM-managed Google OAuth, never user-supplied bearer credentials."""
    folder = os.getenv("CAM_ETSY_IMAGEGEN_DRIVE_FOLDER_ID", "").strip()
    if not _DRIVE_ID.fullmatch(folder):
        raise HTTPException(503, "ImageGen Google Drive folder is not configured")
    provider = build_managed_storage_provider(get_settings())
    if not isinstance(provider, GoogleDriveAssetStorage):
        raise HTTPException(503, "CAM Google Drive managed storage must be connected")
    return provider, folder


async def _body(payload: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(payload), 1024 * 1024):
        yield payload[offset:offset + 1024 * 1024]


def _thumbnail(png: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(png)) as image:
            if image.size != (4500, 5400) or image.mode not in ("RGBA", "RGB", "P"):
                raise ValueError("invalid print artwork")
            image.load()
            reduced = image.convert("RGBA")
            reduced.thumbnail((192, 192), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            reduced.save(buffer, "PNG", optimize=True)
            return buffer.getvalue()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise HTTPException(422, "ImageGen design thumbnail cannot be decoded") from exc


async def store_design_to_drive(
    *, tenant_id: str, job_id: str, asset_id: str, digest: str, png: bytes,
) -> dict:
    """Upload original + thumbnail; store only Drive IDs, no local fallback."""
    if len(png) < 57 or len(png) > _MAX_PNG:
        raise HTTPException(422, "Invalid print PNG size")
    if hashlib.sha256(png).hexdigest() != digest:
        raise HTTPException(422, "ImageGen PNG SHA256 mismatch")
    provider, folder = provider_and_folder()
    preview = _thumbnail(png)
    thumb_hash = hashlib.sha256(preview).hexdigest()
    async def put(label: str, payload: bytes, sha: str):
        stored = await provider.store_asset(StoreAssetInput(
            tenant_id=tenant_id, asset_id=job_id + ":" + asset_id + ":" + label,
            content_hash=sha, filename=job_id + "-" + asset_id + "-" + label + ".png",
            destination_folder_id=folder, content_type="image/png",
            size_bytes=len(payload), body=_body(payload),
        ))
        file_id = str(stored.remote_file_id or "")
        if not _DRIVE_ID.fullmatch(file_id):
            raise HTTPException(502, "Google Drive did not confirm file identity")
        return file_id
    try:
        remote_id = await put("original", png, digest)
        preview_id = await put("thumbnail", preview, thumb_hash)
    except StorageProviderError as exc:
        raise HTTPException(503 if exc.retryable else 502,
                            "Google Drive upload is unavailable") from exc
    return {
        "storage_provider": "google_drive_managed", "drive_file_id": remote_id,
        "drive_thumbnail_id": preview_id, "storage_size_bytes": len(png),
        "thumbnail_sha256": thumb_hash, "storage_folder_id": folder,
    }


async def read_design_from_drive(*, tenant_id: str, asset_id: str, remote_id: str,
                                  sha256: str, max_bytes: int) -> bytes:
    if not _DRIVE_ID.fullmatch(remote_id):
        raise HTTPException(404, "Design Drive identity invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
        raise HTTPException(404, "Design SHA256 invalid")
    provider, _ = provider_and_folder()
    try:
        streamed = await provider.open_asset(OpenStoredAssetInput(
            tenant_id=tenant_id, asset_id=asset_id,
            remote_file_id=remote_id, content_type="image/png",
        ))
        data = bytearray()
        try:
            async for part in streamed.body:
                data.extend(part)
                if len(data) > max_bytes:
                    raise HTTPException(413, "Drive Design image too large")
        finally:
            await streamed.close()
    except StorageProviderError as exc:
        raise HTTPException(503, "Design unavailable on Google Drive") from exc
    image = bytes(data)
    if hashlib.sha256(image).hexdigest() != sha256:
        raise HTTPException(502, "Google Drive Design checksum mismatch")
    return image
