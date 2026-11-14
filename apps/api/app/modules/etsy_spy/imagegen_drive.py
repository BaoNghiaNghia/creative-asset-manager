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
from .thumbnail_memory_cache import ThumbnailMemoryCache

_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_MAX_PNG = 80 * 1024 * 1024
_THUMBNAIL_CACHE = ThumbnailMemoryCache()  # Per-process 16 MiB maximum; never originals.


def provider_and_folder():
    """Use CAM-managed Google OAuth, never user-supplied bearer credentials."""
    folder = os.getenv("CAM_ETSY_IMAGEGEN_DRIVE_FOLDER_ID", "").strip()
    if not _DRIVE_ID.fullmatch(folder):
        raise HTTPException(503, "ImageGen Google Drive folder is not configured")
    expected_email = os.getenv("CAM_ETSY_IMAGEGEN_DRIVE_OWNER_EMAIL", "").strip().lower()
    if not expected_email or "@" not in expected_email:
        raise HTTPException(503, "ImageGen Google Drive owner identity is not configured")
    # The managed OAuth repository keys connections by root_folder_id.
    # Bind this provider ONLY to the ImageGen folder so connecting its
    # Google account never replaces CAM's unrelated Managed Storage OAuth.
    settings = get_settings().model_copy(update={
        "GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID": folder,
    })
    connection = managed_storage_oauth_status(settings)
    # OAuth status intentionally masks account_email. Never compare the
    # masked UI value to an unmasked address; verify the real Drive identity
    # against Google's /about response using the access token below.
    if (connection.get("connected") is not True or
        connection.get("reconnect_required") is True):
        raise HTTPException(503, "Connect CAM Google Drive using the target folder owner account")
    provider = build_managed_storage_provider(settings)
    if not isinstance(provider, GoogleDriveAssetStorage):
        raise HTTPException(503, "CAM Google Drive managed storage must be connected")
    return provider, folder


async def verify_private_drive_destination(provider: GoogleDriveAssetStorage, folder: str) -> None:
    """Require the intended owner, write permission, and Restricted sharing."""
    try:
        token = await provider.get_access_token()
    except StorageProviderError as exc:
        raise HTTPException(503, "ImageGen Drive credentials require reconnection") from exc
    await verify_imagegen_oauth_token(token, folder)


async def verify_imagegen_oauth_token(
    token: str, folder: str, *, restrict_public_sharing: bool = False,
    allow_public_sharing: bool = False,
) -> bool:
    """Validate a scoped OAuth grant before it is saved or used for uploads.

    The Drive folder's public link permissions are never sufficient proof
    that CAM's OAuth account is the right account for design ownership.
    """
    expected_email = os.environ["CAM_ETSY_IMAGEGEN_DRIVE_OWNER_EMAIL"].strip().lower()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as client:
            headers = {"Authorization": f"Bearer {token}"}
            identity_response = await client.get(
                "https://www.googleapis.com/drive/v3/about",
                headers=headers, params={"fields": "user(emailAddress)"},
            )
            response = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{folder}",
                headers=headers,
                params={"supportsAllDrives": "true",
                        "fields": ("id,mimeType,owners(emailAddress),"
                                   "permissions(id,type,role),capabilities(canAddChildren)")},
            )
        if identity_response.status_code != 200:
            raise HTTPException(503, "Cannot verify CAM Google Drive OAuth identity")
        identity = identity_response.json()
        if str((identity.get("user") or {}).get("emailAddress") or "").lower() != expected_email:
            raise HTTPException(503, "CAM OAuth is connected to another Google account")
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
    if not isinstance(permissions, list):
        raise HTTPException(503, "Cannot inspect ImageGen Drive folder sharing")
    public = [permission for permission in permissions
              if permission.get("type") in {"anyone", "domain"}]
    if not public:
        return True
    if not restrict_public_sharing:
        if allow_public_sharing:
            return False
        raise HTTPException(503, "Set ImageGen Drive folder sharing to Restricted")
    # The OAuth initiator explicitly chose ImageGen's write-enabled connection.
    # With the verified folder-owner grant, remove ONLY public/domain grants,
    # never named collaborators or owner privileges.
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{3,128}",
                            str(permission.get("id") or ""))
           for permission in public):
        if allow_public_sharing:
            return False
        raise HTTPException(503, "Cannot identify public Google Drive permissions")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as client:
            for permission in public:
                result = await client.delete(
                    f"https://www.googleapis.com/drive/v3/files/{folder}"
                    f"/permissions/{permission['id']}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"supportsAllDrives": "true"},
                )
                if result.status_code not in {200, 204}:
                    # A Google permissions.delete failure must not discard
                    # otherwise valid owner OAuth, but images stay blocked.
                    if allow_public_sharing:
                        return False
                    raise HTTPException(503, "Could not set ImageGen Drive sharing to Restricted")
            checked = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{folder}",
                headers={"Authorization": f"Bearer {token}"},
                params={"supportsAllDrives": "true",
                        "fields": "id,permissions(id,type,role)"},
            )
        if checked.status_code != 200:
            if allow_public_sharing:
                return False
            raise HTTPException(503, "Could not verify restricted Drive sharing")
        updated = checked.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        if allow_public_sharing:
            return False
        raise HTTPException(503, "Could not verify restricted Drive sharing") from exc
    if updated.get("id") != folder or not isinstance(updated.get("permissions"), list) or any(
        permission.get("type") in {"anyone", "domain"}
        for permission in updated["permissions"]
    ):
        if allow_public_sharing:
            return False
        raise HTTPException(503, "Drive sharing is still public; reconnect after restricting it")
    return True


async def _body(payload: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(payload), 1024 * 1024):
        yield payload[offset:offset + 1024 * 1024]


def _thumbnail(png: bytes, *, native_master: bool = False) -> tuple[bytes, tuple[int, int]]:
    """Validate a master before creating its small, alpha-preserving preview.

    Native master pixels are never resized or re-encoded for storage. Only the
    separate thumbnail is resampled; the full native PNG stays byte-identical.
    """
    try:
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
            if image.format != "PNG":
                raise ValueError("image must be PNG")
            if native_master:
                if (image.mode != "RGBA" or width < 256 or height < 256 or
                    width > 4096 or height > 4096 or width * height > 12_000_000):
                    raise ValueError("invalid native master dimensions or mode")
            elif (image.size != (4500, 5400) or
                  image.mode not in ("RGBA", "RGB", "P")):
                raise ValueError("invalid print artwork")
            image.load()
            reduced = image.convert("RGBA")
            if native_master:
                extrema = reduced.getchannel("A").getextrema()
                if extrema is None or extrema[0] != 0 or extrema[1] == 0:
                    raise ValueError("native master lacks genuine transparency")
            reduced.thumbnail((192, 192), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            reduced.save(buffer, "PNG", optimize=True)
            return buffer.getvalue(), (width, height)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise HTTPException(422, "ImageGen design thumbnail cannot be decoded") from exc


async def store_design_to_drive(
    *, tenant_id: str, job_id: str, asset_id: str, digest: str, png: bytes,
    native_master: bool = False,
) -> dict:
    """Upload immutable master + small thumbnail; no VPS PNG persistence.

    Existing print PNGs continue through the original path. Native master
    uploads require an explicit opt-in and preserve original RGBA pixels.
    """
    limit = 20 * 1024 * 1024 if native_master else _MAX_PNG
    if len(png) < 57 or len(png) > limit:
        raise HTTPException(422, "Invalid design PNG size")
    if hashlib.sha256(png).hexdigest() != digest:
        raise HTTPException(422, "ImageGen PNG SHA256 mismatch")
    if native_master and (png[:8] != b"\x89PNG\r\n\x1a\n" or
                          png[24:26] != b"\x08\x06"):
        raise HTTPException(422, "Native master requires 8-bit RGBA PNG")
    # Validate before any external write, including for malformed PNGs.
    preview, (width, height) = _thumbnail(png, native_master=native_master)
    provider, folder = provider_and_folder()
    await verify_private_drive_destination(provider, folder)
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
        "artwork_kind": "native_master_v2" if native_master else "print_png_v1",
        "native_width": width if native_master else None,
        "native_height": height if native_master else None,
    }


async def read_design_from_drive(*, tenant_id: str, asset_id: str, remote_id: str,
                                  sha256: str, max_bytes: int) -> bytes:
    if not _DRIVE_ID.fullmatch(remote_id):
        raise HTTPException(404, "Design Drive identity invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
        raise HTTPException(404, "Design SHA256 invalid")
    provider, _ = provider_and_folder()
    is_thumbnail = asset_id.endswith(":thumbnail") and max_bytes <= 2 * 1024 * 1024
    cache_key = (tenant_id, remote_id, sha256)
    if is_thumbnail:
        cached = _THUMBNAIL_CACHE.get(cache_key)
        if cached is not None:
            if len(cached) > max_bytes:
                raise HTTPException(413, "Drive Design image too large")
            return cached
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
    if is_thumbnail:
        _THUMBNAIL_CACHE.put(cache_key, image)
    return image
