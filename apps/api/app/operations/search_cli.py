from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import (
    ElasticsearchV3Config,
    ElasticsearchV3Index,
)
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.search.coverage_audit import SearchV3CoverageAudit, SearchV3CoverageRepair
from app.modules.search.operations_repository import SearchOperationRepository
from app.modules.search.operations_service import SearchMaintenanceService

_COMMANDS = {
    "search:rebuild-projections": "rebuild_projections",
    "search:reindex-assets": "reindex_assets",
    "search:rebuild-and-reindex": "rebuild_and_reindex",
}
_AUDIT_COMMAND = "search:audit-coverage"
_REPAIR_COMMAND = "search:repair-coverage"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Creative Asset Manager search operations")
    value.add_argument("command", choices=[*_COMMANDS, _AUDIT_COMMAND, _REPAIR_COMMAND, "search:cancel"])
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
    value.add_argument("--index-generation", choices=("v2", "v3"))
    value.add_argument("--projection-version")
    value.add_argument("--limit", type=int)
    value.add_argument("--after-created-at")
    value.add_argument("--after-analysis-id")
    value.add_argument("--verify-elasticsearch", action="store_true")
    value.add_argument("--output-json", action="store_true")
    value.add_argument("--apply", action="store_true")
    value.add_argument("--repair-projections", action="store_true")
    value.add_argument("--repair-indexes", action="store_true")
    return value


async def execute(args: argparse.Namespace) -> dict:
    settings = get_settings()
    if args.command == _AUDIT_COMMAND:
        return await _audit_coverage(args, settings)
    if args.command == _REPAIR_COMMAND:
        return await _repair_coverage(args, settings)

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
        index_generation = args.index_generation or ("v3" if settings.SEARCH_V3_ENABLED else "v2")
        provider = None
        if reindex and not run.dry_run:
            if not args.elasticsearch_url:
                raise ValueError("--elasticsearch-url is required for reindexing")
            provider = ElasticsearchV3Index(
                ElasticsearchV3Config(
                    args.elasticsearch_url,
                    index_prefix=args.index_prefix,
                    index_generation=index_generation,
                )
            )
        try:
            result = await SearchMaintenanceService(
                repository,
                SearchProjectionBuilder(projection_version=run.target_projection_version),
                index_provider=provider,
                projection_enabled=settings.SEARCH_PROJECTION_ENABLED,
                index_enabled=(settings.ELASTICSEARCH_V2_ENABLED or settings.SEARCH_V3_ENABLED),
                deterministic_active_analysis_enabled=settings.DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED,
                index_lifecycle_enabled=settings.ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED,
            ).run(
                tenant_id=args.tenant_id,
                run_id=run.id,
                index_version=args.index_version,
            )
            return _progress(result)
        finally:
            if provider is not None:
                await provider.__aexit__()


async def _audit_coverage(args: argparse.Namespace, settings) -> dict:
    projection_version = args.projection_version or SearchProjectionBuilder().projection_version
    after_created_at = (
        datetime.fromisoformat(args.after_created_at)
        if args.after_created_at
        else None
    )
    if (after_created_at is None) != (args.after_analysis_id is None):
        raise ValueError(
            "--after-created-at and --after-analysis-id must be provided together"
        )
    provider = None
    if args.verify_elasticsearch:
        base_url = args.elasticsearch_url or settings.ELASTICSEARCH_URL
        if not base_url:
            raise ValueError(
                "--verify-elasticsearch requires --elasticsearch-url or ELASTICSEARCH_URL"
            )
        provider = ElasticsearchV3Index(
            ElasticsearchV3Config(
                base_url,
                index_prefix=args.index_prefix,
                index_generation="v3",
            )
        )
    try:
        with SessionLocal() as session:
            result = await SearchV3CoverageAudit(
                session,
                projection_version=projection_version,
                index=provider,
            ).run(
                tenant_id=args.tenant_id,
                page_size=args.page_size,
                limit=args.limit,
                after_created_at=after_created_at,
                after_analysis_id=args.after_analysis_id,
                verify_elasticsearch=args.verify_elasticsearch,
            )
            return {
                "tenant_id": args.tenant_id,
                "projection_version": projection_version,
                "verify_elasticsearch": args.verify_elasticsearch,
                **result.to_document(),
            }
    finally:
        if provider is not None:
            await provider.__aexit__()


async def _repair_coverage(args: argparse.Namespace, settings) -> dict:
    projection_version = args.projection_version or SearchProjectionBuilder().projection_version
    after_created_at = datetime.fromisoformat(args.after_created_at) if args.after_created_at else None
    if (after_created_at is None) != (args.after_analysis_id is None):
        raise ValueError("--after-created-at and --after-analysis-id must be provided together")
    if args.apply and not (args.repair_projections or args.repair_indexes):
        raise ValueError("--apply requires --repair-projections and/or --repair-indexes")
    provider = None
    if args.verify_elasticsearch:
        base_url = args.elasticsearch_url or settings.ELASTICSEARCH_URL
        if not base_url:
            raise ValueError("--verify-elasticsearch requires --elasticsearch-url or ELASTICSEARCH_URL")
        provider = ElasticsearchV3Index(ElasticsearchV3Config(base_url, index_prefix=args.index_prefix, index_generation="v3"))
    try:
        with SessionLocal() as session:
            result = await SearchV3CoverageRepair(session, projection_version=projection_version, index=provider).repair(
                tenant_id=args.tenant_id, page_size=args.page_size, limit=args.limit,
                after_created_at=after_created_at, after_analysis_id=args.after_analysis_id,
                verify_elasticsearch=args.verify_elasticsearch, apply=args.apply,
                repair_projections=args.repair_projections, repair_indexes=args.repair_indexes,
            )
            if args.apply:
                session.commit()
            return {
                "tenant_id": args.tenant_id, "projection_version": projection_version,
                "verify_elasticsearch": args.verify_elasticsearch, "dry_run": not args.apply,
                **result.to_document(),
            }
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
            "created_at": run.cursor_created_at.isoformat() if run.cursor_created_at else None,
            "analysis_id": run.cursor_analysis_id,
        },
    }


def _print_audit(result: dict) -> None:
    print(
        "scanned={scanned} healthy={healthy} projection_missing={projection_missing} "
        "projection_stale={projection_stale} index_job_missing={index_job_missing} "
        "index_job_failed={index_job_failed} document_missing={document_missing} "
        "skipped={skipped} failed={failed}".format(**result)
    )
    cursor = result["cursor"]
    print(
        "resume: --after-created-at {after_created_at} --after-analysis-id {after_analysis_id}".format(
            **cursor
        )
    )


def main() -> int:
    args = parser().parse_args()
    result = asyncio.run(execute(args))
    if args.command in {_AUDIT_COMMAND, _REPAIR_COMMAND} and not args.output_json:
        _print_audit(result)
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
