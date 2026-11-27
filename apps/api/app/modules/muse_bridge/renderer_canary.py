"""One-time Muse-02 native canary ingestion using CAM-managed private Drive.

This accepts only the operator-specified run and immutable bytes. It never
claims a listing, grants renderer readiness, invokes a renderer or changes
sharing. A canary storage receipt is not a production listing receipt.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

import httpx
from fastapi import HTTPException

from app.domain.providers.contracts import OpenStoredAssetInput, StoreAssetInput, StorageProviderError
from app.modules.etsy_spy.imagegen_drive import provider_and_folder, verify_private_drive_destination
from app.modules.etsy_spy.listing_drive_folders import ROOT_FOLDER_ID, verify_listing_file_provenance
from .story_lite import LITE_DURATION_BOUNDS
from .video_storage import validate_mp4

POLICY = "CAM_MUSE_SHARED_DRIVE_CANARY_V1"
PATH = "/api/v1/muse-bridge/workers/muse-02/renderer-canary"
RUN_ID = "92d937d0-289b-48e0-bee8-49be1151ca3f"
SHA256 = "1f774395399fe59e89535160218b03f80a7e0855cd79d8185eb40a1cb47e68fb"
SIZE = 2735467
FOLDER_ID = "1XL5oEwEpCqFhaAfo5wR07YPOazi0AX1j"
FOLDER_NAME = "muse_02_renderer_canary"
_DRIVE = re.compile(r"[A-Za-z0-9_-]{10,128}\Z")
_API = "https://www.googleapis.com/drive/v3/files"
_LOCK = asyncio.Lock()
_LOG = logging.getLogger(__name__)


def canary_asset_id() -> str:
    # Google Drive appProperties limits each UTF-8 key + value to 124 bytes.
    # Keep the complete UUID (without separator characters) and SHA-256.
    value = f"m02-canary:{RUN_ID.replace('-', '')}:{SHA256}"
    if len(("cam_asset_id" + value).encode("utf-8")) > 124:
        raise HTTPException(503, "Canary storage identity exceeds metadata limit")
    return value

def contract():
    return {"policyVersion": POLICY, "uploadPath": PATH, "method": "POST",
            "contentType": "video/mp4", "runId": RUN_ID, "sha256": SHA256,
            "bytes": SIZE, "runIdHeader": "X-CAM-Canary-Run-Id",
            "sha256Header": "X-CAM-Canary-Sha256",
            "destinationRoot": ROOT_FOLDER_ID, "destinationFolder": FOLDER_NAME,
            "productionClaimAllowedByCanary": False, "nativeBytesPreserved": True}

def validate_request(worker, run_id, sha256, content_type, length):
    if worker != "muse-02":
        raise HTTPException(403, "Canary restricted to authenticated Muse-02")
    if run_id != RUN_ID or sha256 != SHA256:
        raise HTTPException(409, "Canary run or expected SHA not authorized")
    if content_type.split(";", 1)[0].lower() != "video/mp4":
        raise HTTPException(415, "Native video/mp4 required")
    if length is not None and length != str(SIZE):
        raise HTTPException(422, "Canary byte count does not match approved sample")

async def read_native(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > SIZE:
            raise HTTPException(413, "Canary exceeds approved byte count")
        data.extend(chunk)
    return bytes(data)

def verify_bytes(raw):
    if len(raw) != SIZE or hashlib.sha256(raw).hexdigest() != SHA256:
        raise HTTPException(422, "Canary native bytes do not match approved SHA and size")
    meta = validate_mp4(raw, "vertical")
    duration = meta.get("durationSeconds")
    if (meta.get("width") != 720 or meta.get("height") != 1280 or duration is None
            or not LITE_DURATION_BOUNDS[0] <= duration <= LITE_DURATION_BOUNDS[1]
            or abs(duration - 10.0) > 0.1):
        raise HTTPException(422, "Canary native dimensions or duration not verified")
    return meta

async def _metadata(provider, file_id, fields):
    token = await provider.get_access_token()
    try:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token},
                                     timeout=httpx.Timeout(25, connect=8)) as client:
            response = await client.get(f"{_API}/{file_id}",
                                        params={"supportsAllDrives": "true", "fields": fields})
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "Canary Drive metadata unavailable") from exc

async def _check_folder(provider, tenant):
    meta = await _metadata(provider, FOLDER_ID, "id,name,parents,trashed,mimeType,appProperties")
    props = meta.get("appProperties") or {}
    if (meta.get("id") != FOLDER_ID or meta.get("name") != FOLDER_NAME
            or meta.get("trashed") is not False
            or meta.get("mimeType") != "application/vnd.google-apps.folder"
            or ROOT_FOLDER_ID not in (meta.get("parents") or [])
            or props.get("cam_imagegen_node") != "muse-renderer-canary-v1"
            or props.get("cam_tenant") != tenant or props.get("cam_worker") != "muse-02"):
        raise HTTPException(409, "Canary destination identity or provenance mismatch")
    await verify_private_drive_destination(provider, ROOT_FOLDER_ID)
    await verify_private_drive_destination(provider, FOLDER_ID)

async def _readback(provider, tenant, asset_id, file_id):
    meta = await _metadata(provider, file_id, "id,parents,trashed,mimeType,size,permissions(type,role)")
    permissions = meta.get("permissions")
    if (meta.get("id") != file_id or meta.get("trashed") is not False
            or meta.get("mimeType") != "video/mp4"
            or FOLDER_ID not in (meta.get("parents") or [])
            or meta.get("size") != str(SIZE)
            or not isinstance(permissions, list) or not permissions
            or any(not isinstance(p, dict) or p.get("type") not in {"user", "group"}
                   for p in permissions)):
        raise HTTPException(409, "Canary saved file identity or privacy mismatch")
    stream = await provider.open_asset(OpenStoredAssetInput(
        tenant_id=tenant, asset_id=asset_id, remote_file_id=file_id,
        content_type="video/mp4", size_bytes=SIZE))
    digest = hashlib.sha256()
    count = 0
    try:
        async for part in stream.body:
            count += len(part)
            if count > SIZE:
                raise HTTPException(409, "Canary readback exceeds approved byte count")
            digest.update(part)
    finally:
        await stream.close()
    if count != SIZE or digest.hexdigest() != SHA256:
        raise HTTPException(409, "Canary readback SHA or size mismatch")

async def ingest(request, tenant, worker, run_id, sha256):
    validate_request(worker, run_id, sha256, request.headers.get("content-type", ""),
                     request.headers.get("content-length"))
    if _LOCK.locked():
        raise HTTPException(409, "Canary upload in progress", headers={"Retry-After": "15"})
    async with _LOCK:
        try:
            raw = await asyncio.wait_for(read_native(request), timeout=60)
        except TimeoutError as exc:
            raise HTTPException(408, "Canary upload body timed out") from exc
        metadata = verify_bytes(raw)
        provider, root = provider_and_folder()
        if root != ROOT_FOLDER_ID:
            raise HTTPException(503, "Shared CAM Drive root mismatch")
        asset_id = canary_asset_id()
        stage = "destination"
        async def chunks():
            for offset in range(0, len(raw), 1024 * 1024):
                yield raw[offset:offset + 1024 * 1024]
        try:
            await _check_folder(provider, tenant)
            stage = "store_asset"
            stored = await provider.store_asset(StoreAssetInput(
                tenant_id=tenant, asset_id=asset_id, content_hash=SHA256,
                destination_folder_id=FOLDER_ID, filename=f"native-{RUN_ID}.mp4",
                content_type="video/mp4", size_bytes=SIZE, body=chunks()))
            file_id = str(stored.remote_file_id or "")
            if not _DRIVE.fullmatch(file_id) or stored.remote_folder_id != FOLDER_ID:
                raise HTTPException(502, "Canary storage returned unexpected destination")
            stage = "provenance"
            await verify_listing_file_provenance(provider=provider, remote_file_id=file_id,
                folder_id=FOLDER_ID, tenant_id=tenant, asset_id=asset_id, sha256=SHA256)
            stage = "readback"
            await _readback(provider, tenant, asset_id, file_id)
        except StorageProviderError as exc:
            _LOG.warning("Muse-02 canary storage failure stage=%s status=%s retryable=%s",
                         stage, exc.status_code, exc.retryable)
            raise HTTPException(503 if exc.retryable else 502, "Canary storage unavailable") from exc
        return {"ok": True, "policyVersion": POLICY, "workerId": "muse-02",
                "runId": RUN_ID, "sha256": SHA256, "bytes": SIZE,
                "driveFileId": file_id, "driveFolderId": FOLDER_ID,
                "storageVerified": True, "nativeBytesPreserved": True,
                "containerVerified": True, "providerOriginIndependentlyVerified": False,
                "motionVisualQa": "pending_independent_review",
                "productionListingReceiptVerified": False,
                "productionClaimChanged": False, "readinessRegistryChanged": False,
                **metadata}
