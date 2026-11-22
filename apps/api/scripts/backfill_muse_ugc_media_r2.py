"""Bounded, explicit one-time backfill of recent CAM Muse UGC derivatives.

Reads only Drive-verified MP4 receipts, then stages playback/posters to the
existing private R2 bucket. No writes to PostgreSQL, no local persistent media.
Never runs from page view. Default is dry-run.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.providers.contracts import OpenStoredAssetInput
from app.modules.muse_bridge.model import CamMuseJob
from app.modules.muse_bridge.ugc_media_cache import config_for_tenant, available, stage_uploaded
from app.modules.etsy_spy.imagegen_drive import provider_and_folder
from app.modules.etsy_spy.listing_drive_folders import (
    ROOT_FOLDER_ID, verify_listing_file_provenance,
)

_MAX_BYTES = 40 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE = re.compile(r"[A-Za-z0-9_-]{10,128}\Z")


async def download(provider, *, tenant: str, asset: str, remote_id: str,
                   expected_sha: str) -> bytes:
    media = await provider.open_asset(OpenStoredAssetInput(
        tenant_id=tenant, asset_id=asset, remote_file_id=remote_id,
        content_type="video/mp4",
    ))
    chunks = bytearray()
    try:
        async for chunk in media.body:
            chunks.extend(chunk)
            if len(chunks) > _MAX_BYTES:
                raise ValueError("video_too_large")
    finally:
        await media.close()
    result = bytes(chunks)
    if not result or sha256(result).hexdigest() != expected_sha:
        raise ValueError("video_checksum_mismatch")
    return result


async def run(tenant: str, *, limit: int, apply: bool) -> dict[str, int | bool]:
    config = config_for_tenant(tenant)
    result: dict[str, int | bool] = {
        "apply": apply, "scanned": 0, "eligible": 0, "cached": 0,
        "skipped": 0, "failed": 0, "configured": config is not None,
    }
    if config is None:
        return result
    cutoff = datetime.now(timezone.utc) - timedelta(days=29)
    with SessionLocal() as session:
        rows = session.scalars(select(CamMuseJob).where(
            CamMuseJob.tenant_id == tenant,
        ).order_by(CamMuseJob.submitted_at.desc().nullslast()).limit(limit)).all()
        session.rollback()
    if not rows:
        return result
    provider, root = provider_and_folder()
    if root != ROOT_FOLDER_ID:
        return result
    for job in rows:
        result["scanned"] += 1
        created = job.submitted_at or job.created_at
        if created is None:
            result["skipped"] += 1
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            result["skipped"] += 1
            continue
        uploads = (job.output_receipts or {}).get("drive_uploads")
        if not isinstance(uploads, dict) or not job.artwork_sha256:
            result["skipped"] += 1
            continue
        for ratio, aspect in (("9:16", "vertical"), ("16:9", "horizontal")):
            v = uploads.get(ratio)
            if (not isinstance(v, dict) or v.get("storageVerified") is not True
                    or v.get("storage_provider") != "google_drive_managed"):
                continue
            sha = str(v.get("sha256") or "")
            remote = str(v.get("drive_file_id") or "")
            folder = str(v.get("drive_folder_id") or "")
            if not _SHA.fullmatch(sha) or not _DRIVE.fullmatch(remote) or not _DRIVE.fullmatch(folder):
                result["skipped"] += 1
                continue
            if await available(config, tenant=tenant, sha=sha, kind="playback"):
                result["cached"] += 1
                continue
            result["eligible"] += 1
            if not apply:
                continue
            asset_id = f"muse-ugc:{job.listing_key}:{job.artwork_sha256}:{aspect}"
            try:
                await verify_listing_file_provenance(
                    provider=provider, remote_file_id=remote, folder_id=folder,
                    tenant_id=tenant, asset_id=asset_id, sha256=sha,
                )
                content = await download(
                    provider, tenant=tenant, asset=asset_id,
                    remote_id=remote, expected_sha=sha,
                )
                status = await stage_uploaded(
                    config, tenant=tenant, sha=sha, mp4=content,
                )
                if status.get("playback"):
                    result["cached"] += 1
                else:
                    result["failed"] += 1
            except Exception:
                result["failed"] += 1
            finally:
                # Never leak object identifiers or an internal provider error.
                pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", choices=("boostgame",), required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 25:
        parser.error("limit must be 1..25")
    data = asyncio.run(run(args.tenant, limit=args.limit, apply=args.apply))
    print(json.dumps(data, sort_keys=True))
    return 0 if data["configured"] and data["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
