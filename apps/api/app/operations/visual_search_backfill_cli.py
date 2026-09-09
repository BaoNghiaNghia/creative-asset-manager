from __future__ import annotations
import argparse, json, signal
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.processing.repository import ProcessingRepository
from app.modules.visual_search.backfill import VisualSearchBackfillService

def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded visual-search backfill job producer")
    parser.add_argument("--tenant-id", action="append", required=True)
    parser.add_argument("--schema-version", required=True)
    parser.add_argument("--after-asset-id")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-assets", type=int, default=100)
    parser.add_argument("--delay-seconds", type=float, default=0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    stopping = [False]
    signal.signal(signal.SIGINT, lambda *_: stopping.__setitem__(0, True))
    signal.signal(signal.SIGTERM, lambda *_: stopping.__setitem__(0, True))
    output = []
    with SessionLocal() as session:
        service = VisualSearchBackfillService(ProcessingRepository(session), settings=get_settings())
        for tenant_id in sorted(set(args.tenant_id)):
            result = service.run(tenant_id=tenant_id, schema_version=args.schema_version, after_asset_id=args.after_asset_id, batch_size=args.batch_size, max_assets=args.max_assets, delay_seconds=args.delay_seconds, dry_run=not args.execute, stop_requested=lambda: stopping[0])
            output.append({"tenant_id": tenant_id, "dry_run": not args.execute, **result.__dict__})
            if args.execute: session.commit()
            if stopping[0]: break
    print(json.dumps({"results": output}, sort_keys=True))
    return 0
if __name__ == "__main__": raise SystemExit(main())
