from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import SessionLocal
from app.modules.assets.model import ExternalSourceModel
from app.modules.source_sync.scheduler import SourceSyncScheduler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage durable Google Drive source-sync jobs")
    parser.add_argument("command", choices=("source-sync:list", "source-sync:enqueue", "source-sync:enqueue-all"))
    parser.add_argument("--tenant-id")
    parser.add_argument("--source-id")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if args.command == "source-sync:list":
        with SessionLocal() as session:
            statement = select(ExternalSourceModel).where(ExternalSourceModel.source_type == "google_drive")
            if args.tenant_id: statement = statement.where(ExternalSourceModel.tenant_id == args.tenant_id)
            if args.source_id: statement = statement.where(ExternalSourceModel.id == args.source_id)
            values = []
            for source in session.scalars(statement.order_by(ExternalSourceModel.tenant_id, ExternalSourceModel.id)):
                metadata = dict(source.source_metadata or {})
                values.append({"tenant_id": source.tenant_id, "source_id": source.id, "display_name": source.display_name, "credentials_configured": bool(metadata.get("oauth_connection_id") or metadata.get("provider_account_id"))})
        print(json.dumps(values, ensure_ascii=False))
        return 0
    if args.command == "source-sync:enqueue" and (not args.tenant_id or not args.source_id):
        raise SystemExit("source-sync:enqueue requires --tenant-id and --source-id")
    scheduler = SourceSyncScheduler(SessionLocal, settings)
    if args.command == "source-sync:enqueue":
        result = scheduler.enqueue_source(args.tenant_id, args.source_id, full=args.full, dry_run=args.dry_run)
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0 if result.created or result.skipped_reason in {"dry_run", "active_job"} else 1
    with SessionLocal() as session:
        statement = select(ExternalSourceModel.tenant_id, ExternalSourceModel.id).where(ExternalSourceModel.source_type == "google_drive")
        if args.tenant_id: statement = statement.where(ExternalSourceModel.tenant_id == args.tenant_id)
        if args.source_id: statement = statement.where(ExternalSourceModel.id == args.source_id)
        sources = tuple(session.execute(statement).all())
    results = [scheduler.enqueue_source(tenant_id, source_id, full=args.full, dry_run=args.dry_run) for tenant_id, source_id in sources]
    print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
