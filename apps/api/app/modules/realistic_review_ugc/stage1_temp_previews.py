"""Stage 1 lightweight previews in dedicated managed Google Drive temp folder.

Only worker-produced images beneath working/previews/ are uploaded, and only
from the isolated Codex workspace.  The originals are never uploaded and
they cannot be mistaken for final Stage 1 versions.
"""
from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

from PIL import Image, ImageOps

from app.domain.providers.contracts import StoreAssetInput
from app.modules.realistic_review_ugc.generation_handler import _bytes_body
from app.providers.google.storage import GoogleDriveAssetStorage

TEMP_FOLDER_ID = "1HNV_9BbJohoB5hKsGxpj8owNF7jG-5SB"
PREVIEW_MAX_EDGE = 640
PREVIEW_WEBP_QUALITY = 65
PREVIEW_TTL_HOURS = 48
PREVIEW_SOURCE_FOLDER = "working/previews"
logger = logging.getLogger(__name__)


def preview_paths(workspace: Path) -> list[Path]:
    """Avoid symlinks, path traversal and folders outside the job workspace."""
    base = workspace / "working" / "previews"
    if not base.is_dir() or base.is_symlink():
        return []
    real_workspace = workspace.resolve()
    files = []
    for path in sorted(base.rglob("*")):
        if (path.is_file() and not path.is_symlink()
                and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                and path.resolve().is_relative_to(real_workspace)
                and not any(parent.is_symlink() for parent in path.parents if parent != workspace)):
            files.append(path)
    return files


def compact_preview(path: Path) -> bytes:
    """Convert draft to a small WebP preview, even if Skill saved a large PNG."""
    with Image.open(path) as original:
        original.load()
        image = ImageOps.exif_transpose(original)
        image.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE), Image.Resampling.LANCZOS)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=PREVIEW_WEBP_QUALITY, method=4)
    return buffer.getvalue()


async def upload_temp_previews(
    storage: GoogleDriveAssetStorage, *, workspace: Path, tenant_id: str,
    job_id: str, processing_job_id: str,
) -> int:
    if not isinstance(storage, GoogleDriveAssetStorage):
        logger.warning("stage1_temp_previews_requires_managed_google_drive")
        return 0
    count = 0
    for index, path in enumerate(preview_paths(workspace)):
        try:
            blob = compact_preview(path)
            await storage.store_asset(StoreAssetInput(
                tenant_id=tenant_id,
                content_hash=hashlib.sha256(blob).hexdigest(),
                body=_bytes_body(blob),
                asset_id=f"rrugc-stage1-draft:{job_id}:{processing_job_id}:{index}",
                content_type="image/webp",
                size_bytes=len(blob),
                filename=f"stage1_draft_{job_id}_{processing_job_id}_{index:04}.webp",
                destination_folder_id=TEMP_FOLDER_ID,
            ))
            count += 1
        except Exception:
            # Saving draft previews must not invalidate 6 already-persisted
            # final images; independent Drive maintenance will clean uploaded
            # drafts after the TTL.
            logger.exception("stage1_temp_preview_upload_failed job=%s index=%d", job_id, index)
    logger.info("stage1_temp_previews_uploaded job=%s count=%d ttl_hours=%d", job_id, count, PREVIEW_TTL_HOURS)
    return count
