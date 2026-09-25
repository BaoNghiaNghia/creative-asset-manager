from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.search.governance_model import SearchIndexRecordModel
from app.modules.search.index_adoption import SearchV3IndexAdoption
from app.modules.search.index_lifecycle import (
    IndexVerificationError,
    SearchIndexLifecycleService,
    VerificationSpec,
)


_LIFECYCLE_COMMANDS = {"status", "verify", "activate", "rollback"}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Search index governance operations")
    value.add_argument(
        "command",
        choices=("adopt-active-v3", "status", "verify", "activate", "rollback"),
    )
    value.add_argument("--index-prefix", default="creative-assets")
    value.add_argument("--index-name")
    value.add_argument("--elasticsearch-url")
    value.add_argument("--projection-version")
    value.add_argument("--expected-projection-version")
    value.add_argument("--minimum-document-count", type=int, default=1)
    value.add_argument("--expected-document-count", type=int)
    value.add_argument("--document-count-tolerance", type=int, default=0)
    value.add_argument("--maximum-indexing-failures", type=int, default=0)
    value.add_argument("--tenant-id", action="append", default=[])
    value.add_argument("--actor-id", default="operator-cli")
    mode = value.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    value.add_argument("--confirmed", action="store_true")
    return value


def _record(session, index_name: str) -> SearchIndexRecordModel:
    row = session.scalar(
        select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.physical_index_name == index_name
        )
    )
    if row is None:
        raise LookupError(f"search index record not found: {index_name}")
    return row


def _record_document(row: SearchIndexRecordModel) -> dict:
    return {
        "id": row.id,
        "index": row.physical_index_name,
        "index_prefix": row.index_prefix,
        "index_version": row.index_version,
        "projection_version": row.projection_version,
        "state": row.lifecycle_state,
        "document_count": row.document_count,
        "indexing_failure_count": row.indexing_failure_count,
        "verification": row.verification_json,
    }


async def _adopt(args: argparse.Namespace, settings) -> dict:
    if args.dry_run == args.apply:
        raise ValueError("adopt-active-v3 requires exactly one of --dry-run or --apply")
    if args.apply and not args.confirmed:
        raise ValueError("--apply requires --confirmed")
    base_url = args.elasticsearch_url or settings.ELASTICSEARCH_URL
    if not base_url:
        raise ValueError("ELASTICSEARCH_URL or --elasticsearch-url is required")
    projection_version = (
        args.projection_version or SearchProjectionBuilder().projection_version
    )
    async with ElasticsearchV3Index(
        ElasticsearchV3Config(
            base_url,
            index_prefix=args.index_prefix,
            index_generation="v3",
        )
    ) as provider:
        with SessionLocal() as session:
            result = await SearchV3IndexAdoption(session, provider).run(
                index_prefix=args.index_prefix,
                expected_projection_version=projection_version,
                apply=args.apply,
                confirmed=args.confirmed,
            )
            return result.to_document()


async def _lifecycle(args: argparse.Namespace, settings) -> dict:
    if not settings.ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED:
        raise RuntimeError("ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED is false")
    if not args.index_name:
        raise ValueError(f"{args.command} requires --index-name")
    base_url = args.elasticsearch_url or settings.ELASTICSEARCH_URL
    if not base_url:
        raise ValueError("ELASTICSEARCH_URL or --elasticsearch-url is required")

    async with ElasticsearchV3Index(
        ElasticsearchV3Config(
            base_url,
            index_prefix=args.index_prefix,
            index_generation="v3",
        )
    ) as provider:
        with SessionLocal() as session:
            row = _record(session, args.index_name)
            service = SearchIndexLifecycleService(session, provider)

            if args.command == "status":
                aliases = await provider.alias_indices()
                result = _record_document(row)
                result["aliases"] = {
                    key: sorted(value) for key, value in aliases.items()
                }
                return result

            if args.command == "verify":
                row = await service.verify(
                    row.id,
                    VerificationSpec(
                        expected_projection_version=(
                            args.expected_projection_version
                            or args.projection_version
                            or SearchProjectionBuilder().projection_version
                        ),
                        minimum_document_count=args.minimum_document_count,
                        maximum_indexing_failures=args.maximum_indexing_failures,
                        expected_document_count=args.expected_document_count,
                        document_count_tolerance=args.document_count_tolerance,
                        tenant_ids=tuple(args.tenant_id),
                    ),
                    actor_id=args.actor_id,
                )
                session.commit()
                return _record_document(row)

            if args.command == "activate":
                row = await service.activate(row.id, actor_id=args.actor_id)
                session.commit()
                return _record_document(row)

            row = await service.rollback(row.id, actor_id=args.actor_id)
            session.commit()
            return _record_document(row)


async def execute(args: argparse.Namespace) -> dict:
    settings = get_settings()
    if args.command == "adopt-active-v3":
        return await _adopt(args, settings)
    if args.command in _LIFECYCLE_COMMANDS:
        return await _lifecycle(args, settings)
    raise ValueError(f"unsupported command: {args.command}")


def main() -> int:
    try:
        args = parser().parse_args()
        result = asyncio.run(execute(args))
    except (IndexVerificationError, LookupError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    if args.command == "adopt-active-v3":
        return 0 if result["compatible"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
