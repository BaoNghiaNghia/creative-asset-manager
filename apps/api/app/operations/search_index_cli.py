from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.search.index_adoption import SearchV3IndexAdoption


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Search index governance operations")
    value.add_argument("command", choices=("adopt-active-v3",))
    value.add_argument("--index-prefix", required=True)
    value.add_argument("--elasticsearch-url")
    value.add_argument("--projection-version")
    mode = value.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    value.add_argument("--confirmed", action="store_true")
    return value


async def execute(args: argparse.Namespace) -> dict:
    if args.apply and not args.confirmed:
        raise ValueError("--apply requires --confirmed")
    settings = get_settings()
    base_url = args.elasticsearch_url or settings.ELASTICSEARCH_URL
    if not base_url:
        raise ValueError("ELASTICSEARCH_URL or --elasticsearch-url is required")
    projection_version = args.projection_version or SearchProjectionBuilder().projection_version
    async with ElasticsearchV3Index(ElasticsearchV3Config(
        base_url,
        index_prefix=args.index_prefix,
        index_generation="v3",
    )) as provider:
        with SessionLocal() as session:
            result = await SearchV3IndexAdoption(session, provider).run(
                index_prefix=args.index_prefix,
                expected_projection_version=projection_version,
                apply=args.apply,
                confirmed=args.confirmed,
            )
            return result.to_document()


def main() -> int:
    try:
        result = asyncio.run(execute(parser().parse_args()))
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
