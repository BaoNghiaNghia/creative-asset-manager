"""Authenticated RRUGC original-artwork downloads (not thumbnail previews).

All remote storage reads are tenant-scoped by the caller. Enforce deterministic
names, bounded storage consumption and explicit failure (never a partial ZIP).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
import re
import tempfile
from typing import Any
import zipfile

from app.domain.providers.contracts import OpenStoredAssetInput

MAX_ARCHIVE_FILES = 200
MAX_ARCHIVE_BYTES = 450 * 1024 * 1024
LOCAL_TZ = timezone(timedelta(hours=7))


class OutputArchiveError(Exception):
    pass


def output_download_name(entry: dict[str, Any], stage: str) -> str:
    original = PurePosixPath(str(entry.get("output_name") or "").replace("\\", "/")).name
    if not original or original in {".", ".."}:
        original = f"{stage}_v{int(entry['version']):03d}.png"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", original)[:115].strip("._")
    if not stem:
        stem = f"{stage}_v{int(entry['version']):03d}.png"
    if not PurePosixPath(stem).suffix:
        stem += ".png"
    return stem


def archive_filename(stage: str, label: str, now: datetime | None = None) -> str:
    date = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label.strip()).strip("-")[:60]
    return f"{stage}_{slug or 'outputs'}_{date}.zip"


def output_archive_entry(entry: dict[str, Any], stage: str) -> str:
    timestamp = entry["created_at"]
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    date = timestamp.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
    # Prefix the immutable version to guarantee unique paths even when the
    # Skill saves the same name in different generations.
    return f"{date}/v{int(entry['version']):03d}_{output_download_name(entry, stage)}"


async def build_output_archive(storage: Any, *, tenant_id: str, stage: str, job_id: str, rows: list[dict]):
    """Produce a seekable ZIP from original streams; caller closes the spool.

    The archive is assembled fully before HTTP headers are sent, allowing
    upstream failure to surface as 503 instead of a broken partial download.
    The Spool keeps small ZIPs in RAM and spills large ZIPs to temporary disk.
    """
    if not rows:
        raise OutputArchiveError("No completed images available")
    if len(rows) > MAX_ARCHIVE_FILES:
        raise OutputArchiveError("Too many images for one archive")
    announced = sum(max(0, int(row.get("size_bytes") or 0)) for row in rows)
    if announced > MAX_ARCHIVE_BYTES:
        raise OutputArchiveError("Output images exceed archive size limit")
    spool = tempfile.SpooledTemporaryFile(max_size=12 * 1024 * 1024, mode="w+b")
    total = 0
    try:
        with zipfile.ZipFile(spool, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for row in rows:
                remote_id = row["remote_file_id"]
                stream = await storage.open_asset(OpenStoredAssetInput(
                    tenant_id=tenant_id,
                    asset_id=row.get("asset_id") or f"rrugc-output:{stage}:{job_id}:{row['version']}",
                    remote_file_id=remote_id,
                    content_type=row.get("content_type"),
                    size_bytes=row.get("size_bytes"),
                ))
                try:
                    with archive.open(output_archive_entry(row, stage), "w", force_zip64=True) as file:
                        async for chunk in stream.body:
                            total += len(chunk)
                            if total > MAX_ARCHIVE_BYTES:
                                raise OutputArchiveError("Output images exceed archive size limit")
                            file.write(chunk)
                finally:
                    await stream.close()
        spool.seek(0)
        return spool
    except Exception:
        spool.close()
        raise
