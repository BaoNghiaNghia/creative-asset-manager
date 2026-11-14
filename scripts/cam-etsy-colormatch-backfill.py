#!/usr/bin/env python3
"""Explicit, low-priority ColorMatch backfill using existing Drive thumbnails.

Default DRY RUN. Maximum 30 designs per invocation, at least one second
between downloads. Never fetch master PNGs, contact Etsy/AI, alter artwork,
claim image jobs, create release artifacts, or run during Picked requests.

Run (only when the production backend and OAuth are healthy):
  nice -n 15 python scripts/cam-etsy-colormatch-backfill.py \
    --tenant-id creative-assets-prod --limit 10 --scan-limit 500 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
ENV_FILE = Path("/etc/creative-asset-manager/production.env")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _pending(rows: list[dict]) -> list[dict]:
    from app.modules.etsy_spy.color_match_palette import ALGORITHM
    output: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        current = row.get("color_match")
        sha = row.get("output_sha256")
        if (isinstance(current, dict) and current.get("version") == ALGORITHM
                and current.get("output_sha256") == sha):
            continue
        if (row.get("storage_provider") != "google_drive_managed"
                or not isinstance(row.get("id"), str)
                or not isinstance(sha, str) or not _SHA.fullmatch(sha)
                or not isinstance(row.get("thumbnail_sha256"), str)
                or not _SHA.fullmatch(row["thumbnail_sha256"])
                or not isinstance(row.get("drive_thumbnail_id"), str)):
            continue
        output.append(row)
    return output


async def execute(args) -> dict:
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.modules.processing.model import ProcessingJobModel
    from app.modules.etsy_spy.model import EtsySpyPicked
    from app.modules.etsy_spy.color_match import recommend_garment_colors
    from app.modules.etsy_spy.imagegen_drive import read_design_from_drive

    summary = dict(
        mode="apply" if args.apply else "dry_run",
        examined_jobs=0, pending_designs=0, updated=0, skipped=0, errors=0,
    )
    with SessionLocal() as session:
        jobs = session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == args.tenant_id,
            ProcessingJobModel.job_type == "etsy_design_extract",
            ProcessingJobModel.status == "completed",
            ProcessingJobModel.cancellation_requested.is_(False),
        ).order_by(
            ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc(),
        ).offset(args.offset).limit(args.scan_limit)).all()
        summary["examined_jobs"] = len(jobs)
        pending: list[tuple[str, dict]] = []
        for job in jobs:
            payload = job.payload_json if isinstance(job.payload_json, dict) else {}
            for item in _pending(payload.get("design_assets", [])):
                pending.append((job.id, dict(item)))
                if len(pending) >= args.limit:
                    break
            if len(pending) >= args.limit:
                break
        summary["pending_designs"] = len(pending)
        session.rollback()
    if not args.apply:
        return summary

    for job_id, asset in pending:
        try:
            preview = await read_design_from_drive(
                tenant_id=args.tenant_id,
                asset_id=f"{job_id}:{asset['id']}:thumbnail",
                remote_id=asset["drive_thumbnail_id"],
                sha256=asset["thumbnail_sha256"],
                max_bytes=2 * 1024 * 1024,
            )
            color_match = await asyncio.to_thread(
                recommend_garment_colors, preview, asset["output_sha256"],
            )
            if color_match is None:
                summary["skipped"] += 1
                continue
            with SessionLocal() as session:
                job = session.scalar(select(ProcessingJobModel).where(
                    ProcessingJobModel.id == job_id,
                    ProcessingJobModel.tenant_id == args.tenant_id,
                    ProcessingJobModel.status == "completed",
                    ProcessingJobModel.cancellation_requested.is_(False),
                ).with_for_update())
                if job is None or session.get(
                    EtsySpyPicked, (args.tenant_id, job.entity_id),
                ) is None:
                    summary["skipped"] += 1
                    session.rollback()
                    continue
                payload = job.payload_json if isinstance(job.payload_json, dict) else {}
                current = payload.get("design_assets")
                if not isinstance(current, list):
                    summary["skipped"] += 1
                    session.rollback()
                    continue
                revised = []
                changed = False
                for original in current:
                    if (isinstance(original, dict)
                            and original.get("id") == asset["id"]
                            and original.get("output_sha256") == asset["output_sha256"]
                            and original.get("thumbnail_sha256") == asset["thumbnail_sha256"]
                            and original.get("drive_thumbnail_id") == asset["drive_thumbnail_id"]
                            and original in _pending([original])):
                        revised.append({**original, "color_match": color_match})
                        changed = True
                    else:
                        revised.append(original)
                if not changed:
                    summary["skipped"] += 1
                    session.rollback()
                    continue
                job.payload_json = {**payload, "design_assets": revised}
                session.commit()
                summary["updated"] += 1
        except Exception:
            # Never log credentials or a customer image payload. A single
            # transient Drive/decoder failure must not abort the whole batch.
            summary["errors"] += 1
        finally:
            await asyncio.sleep(args.delay_seconds)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--scan-limit", type=int, default=500)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if (not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.tenant_id)
            or not 1 <= args.limit <= 30
            or not args.limit <= args.scan_limit <= 1000
            or args.offset < 0 or not 1.0 <= args.delay_seconds <= 60.0):
        raise SystemExit("invalid_bounds")
    if args.apply:
        if os.name != "posix" or not ENV_FILE.is_file():
            raise SystemExit("production_environment_required")
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=True)
        if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
            raise SystemExit("production_postgresql_required")
    result = asyncio.run(execute(args))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
