#!/usr/bin/env python3
"""Backfill legacy private local Design PNGs (mapped, review_candidate, fast).

Default dry run. With --apply: verifies Picked authorization, immutable
output SHA and local PNG bytes before adding advisory ColorMatch metadata.
Never modifies PNGs, uploads assets, contacts third parties or creates jobs.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
ENV_FILE = Path("/etc/creative-asset-manager/production.env")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{24}\Z")
MAX_IMAGE_BYTES = 80 * 1024 * 1024
MAX_PIXELS = 30_000_000


def fresh(raw: dict) -> bool:
    from app.modules.etsy_spy.color_match_palette import ALGORITHM
    colors = raw.get("color_match")
    return (isinstance(colors, dict) and colors.get("version") == ALGORITHM
            and colors.get("output_sha256") == raw.get("output_sha256")
            and isinstance(colors.get("recommended"), list)
            and len(colors["recommended"]) > 0)


def candidate_records(job) -> list[tuple[str, str, str, Path]]:
    from app.modules.etsy_spy.image_agent import _storage_path
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    source = _storage_path(job.tenant_id, job.id)
    records: list[tuple[str, str, str, Path]] = []
    assets = payload.get("design_assets")
    if isinstance(assets, list):
        for row in assets:
            if not isinstance(row, dict):
                continue
            asset_id, digest = row.get("id"), row.get("output_sha256")
            if (row.get("storage_provider") == "google_drive_managed"
                    or not isinstance(asset_id, str) or not _ID.fullmatch(asset_id)
                    or not isinstance(digest, str) or not _SHA.fullmatch(digest)
                    or fresh(row)):
                continue
            records.append(("mapped", asset_id, digest,
                            source.with_name(f"{job.id}.design.{asset_id}.png")))
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if (result.get("status") == "needs_review" and not fresh(result)
            and isinstance(result.get("output_sha256"), str)
            and _SHA.fullmatch(result["output_sha256"])):
        records.append(("fast", "fast", result["output_sha256"], source))
    review = payload.get("review_candidate") if isinstance(payload.get("review_candidate"), dict) else {}
    if (review.get("status") == "review_candidate" and not fresh(review)
            and isinstance(review.get("output_sha256"), str)
            and _SHA.fullmatch(review["output_sha256"])):
        records.append(("candidate", "candidate", review["output_sha256"],
                        source.with_suffix(".candidate.png")))
    return records


def make_thumbnail(path: Path, digest: str) -> bytes | None:
    from PIL import Image, UnidentifiedImageError
    if not path.is_file() or not (57 <= path.stat().st_size <= MAX_IMAGE_BYTES):
        return None
    hashed = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hashed.update(chunk)
    if hashed.hexdigest() != digest:
        return None
    try:
        with Image.open(path) as image:
            if (image.format != "PNG" or
                    image.width * image.height > MAX_PIXELS or
                    image.width < 1 or image.height < 1):
                return None
            rendered = image.convert("RGBA")
            rendered.thumbnail((192, 192), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            rendered.save(buffer, format="PNG")
            return buffer.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


async def backfill(args) -> dict:
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.modules.processing.model import ProcessingJobModel
    from app.modules.etsy_spy.model import EtsySpyPicked
    from app.modules.etsy_spy.color_match import recommend_garment_colors

    summary = dict(
        mode="apply" if args.apply else "dry_run",
        scanned_jobs=0, pending=0, updated=0, missing_or_mismatch=0,
        skipped=0, errors=0,
    )
    with SessionLocal() as db:
        jobs = db.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == args.tenant_id,
            ProcessingJobModel.job_type == "etsy_design_extract",
            ProcessingJobModel.status == "completed",
            ProcessingJobModel.cancellation_requested.is_(False),
        ).order_by(ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc())
                          .limit(args.scan_limit)).all()
        targets = []
        for job in jobs:
            summary["scanned_jobs"] += 1
            if db.get(EtsySpyPicked, (args.tenant_id, job.entity_id)) is None:
                continue
            for kind, asset_id, digest, file in candidate_records(job):
                targets.append((job.id, kind, asset_id, digest, file))
                if len(targets) >= args.limit:
                    break
            if len(targets) >= args.limit:
                break
        summary["pending"] = len(targets)
        db.rollback()
    if not args.apply:
        return summary

    for job_id, kind, asset_id, digest, file in targets:
        try:
            thumb = await asyncio.to_thread(make_thumbnail, file, digest)
            if thumb is None:
                summary["missing_or_mismatch"] += 1
                continue
            match = await asyncio.to_thread(recommend_garment_colors, thumb, digest)
            if not match:
                summary["skipped"] += 1
                continue
            with SessionLocal() as db:
                job = db.scalar(select(ProcessingJobModel).where(
                    ProcessingJobModel.id == job_id,
                    ProcessingJobModel.tenant_id == args.tenant_id,
                    ProcessingJobModel.job_type == "etsy_design_extract",
                    ProcessingJobModel.status == "completed",
                    ProcessingJobModel.cancellation_requested.is_(False),
                ).with_for_update())
                if job is None or db.get(EtsySpyPicked, (
                    args.tenant_id, job.entity_id,
                )) is None:
                    summary["skipped"] += 1
                    db.rollback()
                    continue
                payload = job.payload_json if isinstance(job.payload_json, dict) else {}
                updated = False
                if kind == "mapped":
                    current = payload.get("design_assets")
                    if isinstance(current, list):
                        revised = []
                        for row in current:
                            if (isinstance(row, dict) and row.get("id") == asset_id
                                    and row.get("output_sha256") == digest
                                    and row.get("storage_provider") != "google_drive_managed"
                                    and not fresh(row)):
                                revised.append({**row, "color_match": match})
                                updated = True
                            else:
                                revised.append(row)
                        if updated:
                            payload = {**payload, "design_assets": revised}
                else:
                    key = "review_candidate" if kind == "candidate" else "result"
                    row = payload.get(key)
                    expected_status = "review_candidate" if kind == "candidate" else "needs_review"
                    if (isinstance(row, dict) and row.get("status") == expected_status
                            and row.get("output_sha256") == digest and not fresh(row)):
                        payload = {**payload, key: {**row, "color_match": match}}
                        updated = True
                if updated:
                    job.payload_json = payload
                    db.commit()
                    summary["updated"] += 1
                else:
                    db.rollback()
                    summary["skipped"] += 1
        except Exception:
            # No secrets, filenames, token values, or customer content in logs.
            summary["errors"] += 1
        finally:
            await asyncio.sleep(args.delay_seconds)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", choices=["creative-assets-prod"], required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--scan-limit", type=int, default=1000)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 10 or not 1 <= args.scan_limit <= 2000 or not 1 <= args.delay_seconds <= 30:
        raise SystemExit("invalid_bounds")
    if args.apply:
        if os.name != "posix" or not ENV_FILE.is_file():
            raise SystemExit("production_host_required")
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=True)
        if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
            raise SystemExit("production_postgresql_required")
    print(json.dumps(asyncio.run(backfill(args)), sort_keys=True))


if __name__ == "__main__":
    main()
