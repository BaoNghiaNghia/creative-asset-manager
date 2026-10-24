"""Authenticated ZIP preview/import and versioned hat photo delivery."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.hat_stock_sync import (
    EXTENSIONS, MAX_ZIP_BYTES, HatStockSyncError, apply_archive,
    list_stock, read_managed_stock, read_versioned_stock, scan_archive,
)

router = APIRouter(prefix="/products/hat-stock")
READ = require_permission("realistic_review_ugc.read")
CONFIGURE = require_permission("realistic_review_ugc.configure")


async def _zip_content(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Upload an image ZIP file.")
    payload = await file.read(MAX_ZIP_BYTES + 1)
    if len(payload) > MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="Image ZIP is larger than 120 MB.")
    return payload


@router.get("/catalog")
def stock_catalog(principal: CurrentPrincipal = Depends(READ)):
    catalog = list_stock(get_settings())
    catalog["can_sync"] = bool(principal.platform_admin or
                               "realistic_review_ugc.configure" in principal.effective_permissions)
    return catalog


@router.post("/preview")
async def stock_preview(
    file: UploadFile = File(...), principal: CurrentPrincipal = Depends(CONFIGURE),
):
    payload = await _zip_content(file)
    try:
        return scan_archive(payload)[0]
    except HatStockSyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/apply")
async def stock_apply(
    file: UploadFile = File(...),
    preview_sha256: str = Form(..., min_length=64, max_length=64),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    if not re.fullmatch(r"[a-f0-9]{64}", preview_sha256):
        raise HTTPException(status_code=422, detail="Invalid ZIP preview fingerprint.")
    payload = await _zip_content(file)
    try:
        return apply_archive(payload, get_settings(), expected_sha256=preview_sha256)
    except HatStockSyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Hat stock storage unavailable.") from exc


@router.get("/{color}/{view}")
def stock_image(
    color: str, view: str, v: str | None = None,
    principal: CurrentPrincipal = Depends(READ),
):
    result = (read_versioned_stock(get_settings(), color, view, v)
              if v is not None else read_managed_stock(get_settings(), color, view))
    if result is None:
        raise HTTPException(status_code=404, detail="Hat stock view not found.")
    path, revision = result
    media_type = EXTENSIONS[path.suffix.lower()]
    return FileResponse(
        path, media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400", "ETag": '"' + revision + '"', "Vary": "Cookie"},
    )
