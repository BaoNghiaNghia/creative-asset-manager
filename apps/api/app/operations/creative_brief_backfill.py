"""Operator backfill for already-picked Stage 1 keywords.

Runs against the configured application DB. Does not generate images or retry
failed briefs; processing workers perform the queued text jobs later.
"""
import argparse
import json
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel
from app.modules.realistic_review_ugc.creative_brief_service import queue_picked_backfill


def main(argv=None):
    parser = argparse.ArgumentParser(description="Queue Creative Briefs for picked Stage 1 keywords")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()
    if not args.tenant_id.strip() or not 1 <= args.batch_size <= 500:
        parser.error("tenant-id and batch-size 1..500 are required")
    if args.execute and (not settings.DATABASE_URL or not settings.DATABASE_URL.startswith("postgresql")):
        parser.error("Execute requires explicitly configured PostgreSQL.")
    with SessionLocal() as session:
        count = int(session.scalar(select(func.count()).select_from(RrugcKeywordVolumeModel).where(
            RrugcKeywordVolumeModel.tenant_id == args.tenant_id,
            RrugcKeywordVolumeModel.picked.is_(True),
        )) or 0)
        if not args.execute:
            print(json.dumps({"dry_run": True, "picked_keywords": count}, sort_keys=True))
            return 0
        queued = 0
        skipped = 0
        for offset in range(0, count, args.batch_size):
            result = queue_picked_backfill(
                session, tenant_id=args.tenant_id,
                offset=offset, limit=args.batch_size, settings=settings,
            )
            queued += result["queued"]
            skipped += result["skipped"]
        print(json.dumps({
            "dry_run": False, "picked_keywords": count,
            "queued": queued, "skipped": skipped,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
