from __future__ import annotations

import argparse
import json

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_search.backfill import VideoAnalysisBackfillService


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill metadata-only video analysis jobs")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--source-asset-id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        result = VideoAnalysisBackfillService(
            ProcessingRepository(session), settings=get_settings()
        ).run(tenant_id=args.tenant_id, source_asset_id=args.source_asset_id, limit=args.limit, dry_run=args.dry_run)
        if not args.dry_run:
            session.commit()
        print(json.dumps(result.__dict__, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
