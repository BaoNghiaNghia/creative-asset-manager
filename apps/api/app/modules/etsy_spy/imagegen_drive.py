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

import httpx

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.domain.providers.contracts import OpenStoredAssetInput, StoreAssetInput, StorageProviderError
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.modules.storage.managed_oauth import managed_storage_oauth_status
from app.providers.google.storage import GoogleDriveAssetStorage

_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_MAX_PNG = 80 * 1024 * 1024


def provider_and_folder():
    """Use CAM-managed Google OAuth, never user-supplied bearer credentials."""
    folder = os.getenv("CAM_ETSY_IMAGEGEN_DRIVE_FOLDER_ID", "").strip()
    if not _DRIVE_ID.fullmatch(folder):
        raise HTTPException(503, "ImageGen Google Drive folder is not configured")
    expected_email = os.getenv("CAM_ETSY_IMAGEGEN_DRIVE_OWNER_EMAIL", "").strip().lower()
    if not expected_email or "@" not in expected_email:
        raise HTTPException(503, "ImageGen Google Drive owner identity is not configured")
    settings = get_settings()
    connection = managed_storage_oauth_status(settings)
    if (connection.get("connected") is not True or
        connection.get("reconnect_required") is True or
        str(connection.get("account_email") or "").lower() != expected_email):
        raise HTTPException(503, "Connect CAM Google Drive using the target folder owner account")
    provider = build_managed_storage_provider(settings)
    if not isinstance(provider, GoogleDriveAssetStorage):
        raise HTTPException(503, "CAM Google Drive managed storage must be connected")
    return provider, folder


async def verify_private_drive_destination(provider: GoogleDriveAssetStorage, folder: str) -> None:
    """Require the intended owner, write permission, and Restricted sharing.

    The Drive folder's public link permissions are never sufficient proof
    that CAM's OAuth account is the right account for design ownership.
    """
    expected_email = os.environ["CAM_ETSY_IMAGEGEN_DRIVE_OWNER_EMAIL"].strip().lower()
    try:
        token = await provider.get_access_token()
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as client:
            response = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{folder}",
                headers={"Authorization": f"Bearer {token}"},
                params={"supportsAllDrives": "true",
                        "fields": ("id,mimeType,owners(emailAddress),"
                                   "permissions(type,role),capabilities(canAddChildren)")},
            )
        if response.status_code != 200:
            raise HTTPException(503, "ImageGen Google Drive destination is not accessible")
        data = response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(503, "ImageGen Google Drive destination validation failed") from exc
    if (data.get("id") != folder or
        data.get("mimeType") != "application/vnd.google-apps.folder" or
        data.get("capabilities", {}).get("canAddChildren") is not True or
        not any(str(item.get("emailAddress") or "").lower() == expected_email
                for item in data.get("owners", []))):
        raise HTTPException(503, "ImageGen Drive folder owner or write access differs")
    permissions = data.get("permissions")
    if not isinstance(permissions, list) or any(
        item.get("type") in {"anyone", "domain"} for item in permissions
    ):
        raise HTTPException(503, "Set ImageGen Drive folder sharing to Restricted")


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
    await verify_private_drive_destination(provider, folder)
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
