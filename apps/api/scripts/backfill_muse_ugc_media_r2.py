"""Bounded, explicit one-time backfill of recent CAM Muse UGC derivatives.

Reads only Drive-verified MP4 receipts, then stages unchanged native playback to the
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

from sqlalchemy import and_, select

from app.core.database import SessionLocal
from app.domain.providers.contracts import OpenStoredAssetInput
from app.modules.muse_bridge.model import CamMuseJob
from app.modules.etsy_spy.model import EtsySpyPicked
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


async def run(tenant: str, *, limit: int, apply: bool, offset: int = 0,
              posters_only: bool = True) -> dict[str, int | bool]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}", tenant):
        raise ValueError("invalid tenant")
    if not 1 <= limit <= 25 or offset < 0:
        raise ValueError("invalid batch bounds")
    kinds = ("playback",)
    config = config_for_tenant(tenant)
    result: dict[str, int | bool] = {
        "apply": apply, "scanned": 0, "eligible": 0, "cached": 0,
        "skipped": 0, "failed": 0, "configured": config is not None,
        "missing_playback": 0, "missing_posters": 0,
        "offset": offset, "next_offset": offset, "has_more": False,
        "posters_only": posters_only,
    }
    if posters_only:
        # Keep legacy invocation compatible without reading/downloading MP4s.
        result["poster_backfill_disabled"] = True
        return result
    if config is None:
        return result
    cutoff = datetime.now(timezone.utc) - timedelta(days=29)
    with SessionLocal() as session:
        # Scalar snapshots survive rollback; no detached ORM rows and no
        # database transaction across Drive/R2 I/O. Stable listing ordering
        # supports bounded, repeatable passes across the whole Picked set.
        rows = session.execute(select(
            CamMuseJob.listing_key, CamMuseJob.artwork_sha256,
            CamMuseJob.submitted_at, CamMuseJob.created_at,
            CamMuseJob.output_receipts,
        ).join(EtsySpyPicked, and_(
            EtsySpyPicked.tenant_id == CamMuseJob.tenant_id,
            EtsySpyPicked.listing_key == CamMuseJob.listing_key,
        )).where(
            CamMuseJob.tenant_id == tenant,
            CamMuseJob.output_receipts.is_not(None),
        ).order_by(CamMuseJob.listing_key).offset(offset).limit(limit + 1)).all()
        session.rollback()
    result["has_more"] = len(rows) > limit
    rows = rows[:limit]
    result["next_offset"] = offset + len(rows)
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
        if (not isinstance(uploads, dict) or not re.fullmatch(r"[0-9]{5,20}", job.listing_key)
                or not _SHA.fullmatch(job.artwork_sha256 or "")):
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
            # Existing playback stays untouched; native ingest uses
            # create-once R2 conditional PUT and fixed retention.
            present = {
                kind: await available(config, tenant=tenant, sha=sha, kind=kind)
                for kind in kinds
            }
            if all(present.values()):
                result["cached"] += 1
                continue
            result["eligible"] += 1
            if "playback" in present and not present["playback"]:
                result["missing_playback"] += 1
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
                status = await stage_uploaded(config, tenant=tenant, sha=sha, mp4=content)
                if all(present[kind] or status.get(kind) for kind in kinds):
                    result["cached"] += 1
                else:
                    # Preserve existing R2 objects; do not overwrite playback.
                    result["failed"] += 1
            except Exception:
                result["failed"] += 1
            finally:
                # Never leak object identifiers or an internal provider error.
                pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="Exact configured R2 tenant ID")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--include-playback", action="store_true",
                        help="Backfill original MP4 bytes; legacy poster-only mode is disabled")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 25:
        parser.error("limit must be 1..25")
    if args.offset < 0 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}", args.tenant):
        parser.error("invalid tenant or offset")
    data = asyncio.run(run(args.tenant, limit=args.limit, offset=args.offset,
                           apply=args.apply, posters_only=not args.include_playback))
    print(json.dumps(data, sort_keys=True))
    return 0 if data["configured"] and data["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())