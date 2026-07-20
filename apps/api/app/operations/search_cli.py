from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import (
    ElasticsearchV2Config,
    ElasticsearchV2Index,
)
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.search.operations_repository import SearchOperationRepository
from app.modules.search.operations_service import SearchMaintenanceService

_COMMANDS = {
    "search:rebuild-projections": "rebuild_projections",
    "search:reindex-assets": "reindex_assets",
    "search:rebuild-and-reindex": "rebuild_and_reindex",
}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Creative Asset Manager search operations")
    value.add_argument("command", choices=[*_COMMANDS, "search:cancel"])
    value.add_argument("--tenant-id", required=True)
    value.add_argument("--run-id")
    value.add_argument("--metadata-profile")
    value.add_argument("--current-projection-version")
    value.add_argument("--asset-id", action="append", default=[])
    value.add_argument("--only-missing", action="store_true")
    value.add_argument("--only-failed", action="store_true")
    value.add_argument("--dry-run", action="store_true")
    value.add_argument("--page-size", type=int, default=100)
    value.add_argument("--target-projection-version", default="search-projection-v1")
    value.add_argument("--index-version")
    value.add_argument("--elasticsearch-url")
    value.add_argument("--index-prefix", default="creative-assets")
    return value


async def execute(args: argparse.Namespace) -> dict:
    settings = get_settings()
    with SessionLocal() as session:
        repository = SearchOperationRepository(session)
        if args.command == "search:cancel":
            if not args.run_id:
                raise ValueError("--run-id is required for search:cancel")
            run = repository.request_cancellation(args.tenant_id, args.run_id)
            session.commit()
            return _progress(run)

        operation_type = _COMMANDS[args.command]
        if args.only_failed and not args.run_id:
            raise ValueError("--only-failed requires --run-id")
        if args.run_id:
            run = repository.get_run(args.tenant_id, args.run_id)
            operation_type = run.operation_type
            if args.only_failed:
                run.filters_json = {**run.filters_json, "only_failed": True}
                run.status = "pending"
                run.cancellation_requested = False
                session.commit()
        else:
            run = repository.create_run(
                tenant_id=args.tenant_id,
                operation_type=operation_type,
                filters={
                    "metadata_profile": args.metadata_profile,
                    "current_projection_version": args.current_projection_version,
                    "asset_ids": sorted(set(args.asset_id)),
                    "only_missing": args.only_missing,
                    "only_failed": False,
                },
                target_projection_version=args.target_projection_version,
                page_size=args.page_size,
                dry_run=args.dry_run,
            )
            session.commit()

        reindex = operation_type in {"reindex_assets", "rebuild_and_reindex"}
        provider = None
        if reindex and not run.dry_run:
            if not args.elasticsearch_url:
                raise ValueError("--elasticsearch-url is required for reindexing")
            provider = ElasticsearchV2Index(
                ElasticsearchV2Config(
                    args.elasticsearch_url,
                    index_prefix=args.index_prefix,
                )
            )
        try:
            result = await SearchMaintenanceService(
                repository,
                SearchProjectionBuilder(
                    projection_version=run.target_projection_version
                ),
                index_provider=provider,
                projection_enabled=settings.SEARCH_PROJECTION_ENABLED,
                index_enabled=settings.ELASTICSEARCH_V2_ENABLED,
            ).run(
                tenant_id=args.tenant_id,
                run_id=run.id,
                index_version=args.index_version,
            )
            return _progress(result)
        finally:
            if provider is not None:
                await provider.__aexit__()


def _progress(run) -> dict:
    return {
        "run_id": run.id,
        "tenant_id": run.tenant_id,
        "operation": run.operation_type,
        "status": run.status,
        "dry_run": run.dry_run,
        "target_index": run.target_index,
        "alias_switch": run.alias_switch_json,
        "scanned": run.scanned_count,
        "processed": run.processed_count,
        "succeeded": run.succeeded_count,
        "failed": run.failed_count,
        "skipped": run.skipped_count,
        "cursor": {
            "created_at": (
                run.cursor_created_at.isoformat() if run.cursor_created_at else None
            ),
            "analysis_id": run.cursor_analysis_id,
        },
    }


def main() -> int:
    args = parser().parse_args()
    print(json.dumps(asyncio.run(execute(args)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
