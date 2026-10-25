#!/usr/bin/env python3
"""Read-only Slot 7 stock census by default; --apply queues only stale completed jobs.

Run under the established production DB role, with DATABASE_URL explicitly set.
NEVER changes source revisions, requeues unrelated skills, or deletes prior output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.realistic_review_ugc.colorways import (
    PRODUCTS_COLORS, PRODUCTS_COLOR_KEYS, ColorwayService, _stock_bytes, PRODUCTS_STAGE1_SELECTED_ROOT,
)
from app.modules.realistic_review_ugc.hat_stock_sync import read_managed_stock
from app.modules.realistic_review_ugc.slot7_front_stock import read_slot7_front
from app.modules.realistic_review_ugc.model import RrugcColorwayJobModel, RrugcSourcePlanModel


def inspect(session: Session, settings, *, tenant_id: str | None = None) -> tuple[dict, list]:
    query = select(RrugcColorwayJobModel, RrugcSourcePlanModel).join(
        RrugcSourcePlanModel,
        (RrugcSourcePlanModel.id == RrugcColorwayJobModel.source_plan_id)
        & (RrugcSourcePlanModel.tenant_id == RrugcColorwayJobModel.tenant_id)
        & (RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT),
    ).where(RrugcColorwayJobModel.color_key.in_(PRODUCTS_COLOR_KEYS)).order_by(
        RrugcColorwayJobModel.source_plan_id, RrugcColorwayJobModel.color_key)
    if tenant_id:
        query = query.where(RrugcColorwayJobModel.tenant_id == tenant_id)
    rows = list(session.execute(query))
    tenants = {job.tenant_id for job, _ in rows}
    if len(tenants) > 1 and not tenant_id:
        raise RuntimeError("Multiple tenants have Products Slot 7 jobs; specify --tenant-id")
    # Consistency gate: all 12 Products Natural colorways must have genuine ≥1000px
    # Slot 7 fronts. The four supplier fronts do NOT masquerade as 3-angle stock.
    fingerprints = {}
    colors = []
    for color, _name in PRODUCTS_COLORS:
        from PIL import Image
        from io import BytesIO

        data = _stock_bytes(settings, color)
        with Image.open(BytesIO(data)) as im:
            width, height = im.size
        if min(width, height) < 1000:
            raise RuntimeError("Slot 7 color still uses low-resolution stock: " + color)
        managed = read_managed_stock(settings, color, "front")
        supplier = read_slot7_front(settings, color)
        if not managed and not supplier:
            raise RuntimeError("Slot 7 high-resolution import unverified: " + color)
        source = "3-angle managed" if managed else "Slot 7 front-only"
        fingerprints[color] = hashlib.sha256(data).hexdigest()
        colors.append({"color": color, "source": source, "dimensions": [width, height],
                       "sha256_prefix": fingerprints[color][:16]})
    stale = [
        job for job, plan in rows
        if (plan.source_revision == job.source_revision
            and job.status == "completed"
            and job.output_remote_file_id
            and fingerprints.get(job.color_key) != job.stock_sha256)
    ]
    report = {
        "color_count": len(colors), "colors": colors,
        "products_colorway_jobs": len(rows),
        "existing_output_files": sum(bool(job.output_remote_file_id) for job, _ in rows),
        "matching_jobs": sum(fingerprints.get(job.color_key) == job.stock_sha256 for job, _ in rows),
        "stale_completed_jobs": len(stale),
        "skipped_active_or_missing": len(rows) - sum(
            bool(job.output_remote_file_id and job.status == "completed") for job, _ in rows
        ),
        "designs_with_jobs": len({job.source_plan_id for job, _ in rows}),
        "tenant_count": len(tenants),
        "stale_colors": sorted({job.color_key for job in stale}),
    }
    return report, stale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Queue ONLY stale completed outputs")
    ap.add_argument("--max-jobs", type=int, default=3, help="Bounded generation batch, 1..12")
    ap.add_argument("--tenant-id", default=None, help="Mandatory when multiple tenants exist")
    args = ap.parse_args()
    if not 1 <= args.max_jobs <= 12:
        ap.error("--max-jobs must be between 1 and 12")
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        print("ERROR: Production PostgreSQL DATABASE_URL is required", file=sys.stderr)
        return 2
    settings = get_settings()
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 7})
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            report, stale = inspect(session, settings, tenant_id=args.tenant_id)
            report["mode"] = "apply" if args.apply else "preview"
            report["queued_job_ids"] = []
            if args.apply:
                # Recheck every output via a locked service call; does not
                # override job policy or the processing claimer's fairness.
                svc = ColorwayService(session, settings)
                for job in stale[: args.max_jobs]:
                    previous_output = job.output_remote_file_id
                    refreshed = svc.regenerate(tenant_id=job.tenant_id, job_id=job.id)
                    if refreshed.output_remote_file_id != previous_output:
                        raise RuntimeError("Existing completed image unexpectedly changed")
                    report["queued_job_ids"].append(job.id)
            print(json.dumps(report, sort_keys=True, indent=2))
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())