from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Mapping

from app.core.config import get_settings
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Provision the dedicated VIDEO Elasticsearch v3 index")
    value.add_argument("--version", required=True)
    value.add_argument("--index-prefix")
    value.add_argument("--elasticsearch-url")
    mode = value.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    value.add_argument("--confirmed", action="store_true")
    return value


def _segments_are_nested(mapping: Mapping[str, Any], target: str) -> bool:
    value = mapping.get(target, mapping)
    if not isinstance(value, Mapping):
        return False
    mappings = value.get("mappings")
    if not isinstance(mappings, Mapping):
        return False
    properties = mappings.get("properties")
    return isinstance(properties, Mapping) and properties.get("segments", {}).get("type") == "nested"


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply and not args.confirmed:
        raise ValueError("--apply requires --confirmed")
    settings = get_settings()
    base_url = args.elasticsearch_url or settings.ELASTICSEARCH_URL
    if not base_url:
        raise ValueError("ELASTICSEARCH_URL or --elasticsearch-url is required")
    prefix = args.index_prefix or settings.ELASTICSEARCH_INDEX_PREFIX
    index = VideoSearchElasticsearchIndex(ElasticsearchV3Config(
        base_url, index_prefix=prefix, index_generation="v3"
    ))
    try:
        target = index.physical_index_name(args.version)
        result: dict[str, Any] = {
            "apply": bool(args.apply),
            "target_index": target,
            "read_alias": index.read_alias,
            "write_alias": index.write_alias,
            "segments_nested": None,
            "aliases_switched": False,
        }
        if args.dry_run:
            return result
        await index.ensure_index(args.version)
        mapping = await index.index_mapping(target)
        if not _segments_are_nested(mapping, target):
            raise ValueError("target video index has incompatible segments mapping")
        result["segments_nested"] = True
        await index.switch_aliases(target)
        aliases = await index.alias_indices()
        if aliases["read"] != {target} or aliases["write"] != {target}:
            raise RuntimeError("video aliases did not switch to target index")
        result["aliases_switched"] = True
        return result
    finally:
        await index.aclose()


def main() -> int:
    try:
        result = asyncio.run(execute(parser().parse_args()))
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
