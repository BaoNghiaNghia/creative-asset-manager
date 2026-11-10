#!/usr/bin/env python3
"""Explicit, bounded production queue-only backfill for tenant Picked designs.

Never claims a job, downloads an Etsy image, starts an AI API or raises
CAM_ETSY_EXTRACTION_DAILY_CAP. Idempotency is enforced by ProcessingJobModel.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from dotenv import load_dotenv

ENV = Path("/etc/creative-asset-manager/production.env")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True, choices=["creative-assets-prod"])
    parser.add_argument("--apply", action="store_true", help="Create missing queue rows")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    if not (1 <= args.limit <= 200):
        raise SystemExit("invalid_limit")
    if not ENV.is_file() or os.name != "posix":
        raise SystemExit("production_host_required")
    load_dotenv(ENV, override=True)
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("production_postgresql_required")
    from sqlalchemy import select, func
    from app.core.database import SessionLocal
    from app.modules.etsy_spy.router import _enqueue_missing_picked
    from app.modules.etsy_spy.extraction import extraction_enabled, EXTRACTION_JOB_TYPE
    from app.modules.etsy_spy.image_agent import storage_capacity, daily_cap
    from app.modules.etsy_spy.model import EtsySpyPicked
    from app.modules.processing.model import ProcessingJobModel
    if not extraction_enabled() or not storage_capacity()["ready"]:
        raise SystemExit("extraction_storage_or_feature_disabled")
    with SessionLocal() as db:
        picked = db.scalar(select(func.count()).select_from(EtsySpyPicked).where(
            EtsySpyPicked.tenant_id == args.tenant_id)) or 0
        before = db.scalar(select(func.count()).select_from(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == args.tenant_id,
            ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
            ProcessingJobModel.cancellation_requested.is_(False))) or 0
        total = {"created": 0, "ready": 0, "skipped_no_source": 0}
        # This operator script only adds pending DB rows. The external Windows
        # Image Agent still enforces its independent daily claim limit.
        for _ in range(20):
            result = _enqueue_missing_picked(db, args.tenant_id,
                args.limit, preview=not args.apply)
            for key in total:
                total[key] += result[key]
            if not args.apply or result["created"] == 0 or result["ready"] < args.limit:
                break
        after = db.scalar(select(func.count()).select_from(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == args.tenant_id,
            ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
            ProcessingJobModel.cancellation_requested.is_(False))) or 0
    print(json.dumps({"tenant_id": args.tenant_id,
        "mode": "queue_only_apply" if args.apply else "queue_only_dry_run",
        "picked": int(picked), "jobs_before": int(before),
        "jobs_after": int(after), **total, "daily_claim_cap_unchanged": daily_cap(),
        "triggered_extraction": False, "print_ready": False}))


if __name__ == "__main__":
    main()
