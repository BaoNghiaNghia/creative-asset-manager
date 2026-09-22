from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import httpx

from app.modules.visual_search.model_spec import (
    SIGLIP_V1_REVISION,
    VISUAL_SEARCH_V1_DESCRIPTOR,
)


_CORE_MODEL_FILES = (
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_v1_model_snapshot(path: Path) -> dict[str, Any]:
    resolved = path.expanduser()
    files: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for name in _CORE_MODEL_FILES:
        candidate = resolved / name
        if not candidate.is_file():
            missing.append(name)
            continue
        files[name] = {
            "size_bytes": candidate.stat().st_size,
            "sha256": _sha256(candidate),
        }

    return {
        "path": str(resolved),
        "expected_revision": SIGLIP_V1_REVISION,
        "revision_path_matches": resolved.name == SIGLIP_V1_REVISION,
        "core_files": files,
        "missing_core_files": missing,
        "ready": (
            resolved.is_dir()
            and resolved.name == SIGLIP_V1_REVISION
            and not missing
        ),
    }


def _mapping_vector_definition(
    mapping_payload: Mapping[str, Any],
    index_name: str,
) -> Mapping[str, Any] | None:
    index = mapping_payload.get(index_name)
    if not isinstance(index, Mapping):
        return None
    mappings = index.get("mappings")
    if not isinstance(mappings, Mapping):
        return None
    properties = mappings.get("properties")
    if not isinstance(properties, Mapping):
        return None
    vector = properties.get("visual_embedding")
    return vector if isinstance(vector, Mapping) else None


async def inspect_v1_elasticsearch(
    *,
    elasticsearch_url: str,
    index_prefix: str,
    index_generation: str,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    alias = (
        f"{index_prefix}-visual-"
        f"{VISUAL_SEARCH_V1_DESCRIPTOR.embedding_schema_version}-"
        f"{index_generation}-read"
    )
    async with httpx.AsyncClient(
        base_url=elasticsearch_url.rstrip("/"),
        timeout=timeout_seconds,
        transport=transport,
    ) as client:
        response = await client.get(f"/_alias/{alias}")
        if response.status_code == 404:
            return {
                "alias": alias,
                "target_indices": [],
                "indices": [],
                "ready": False,
                "reason": "v1 read alias is unavailable",
            }
        response.raise_for_status()
        alias_payload = response.json()
        if not isinstance(alias_payload, dict):
            raise RuntimeError("Elasticsearch v1 alias response is malformed")

        target_indices = sorted(alias_payload)
        indices: list[dict[str, Any]] = []
        all_compatible = bool(target_indices)
        for index_name in target_indices:
            mapping_response = await client.get(f"/{index_name}/_mapping")
            mapping_response.raise_for_status()
            mapping_payload = mapping_response.json()
            if not isinstance(mapping_payload, dict):
                raise RuntimeError("Elasticsearch v1 mapping response is malformed")
            vector = _mapping_vector_definition(mapping_payload, index_name)

            count_response = await client.get(f"/{index_name}/_count")
            count_response.raise_for_status()
            count_payload = count_response.json()
            if not isinstance(count_payload, dict):
                raise RuntimeError("Elasticsearch v1 count response is malformed")
            count = count_payload.get("count")
            compatible = bool(
                isinstance(vector, Mapping)
                and vector.get("type") == "dense_vector"
                and vector.get("dims") == VISUAL_SEARCH_V1_DESCRIPTOR.dimension
                and vector.get("similarity")
                == VISUAL_SEARCH_V1_DESCRIPTOR.similarity
                and isinstance(count, int)
                and count >= 0
            )
            all_compatible = all_compatible and compatible
            indices.append(
                {
                    "name": index_name,
                    "document_count": count,
                    "vector": dict(vector) if vector is not None else None,
                    "compatible": compatible,
                }
            )

    return {
        "alias": alias,
        "target_indices": target_indices,
        "indices": indices,
        "ready": all_compatible,
    }


async def collect_rollback_proof(
    *,
    v1_model_path: Path,
    elasticsearch_url: str,
    index_prefix: str = "creative-assets",
    index_generation: str = "v3",
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    model = inspect_v1_model_snapshot(v1_model_path)
    elasticsearch = await inspect_v1_elasticsearch(
        elasticsearch_url=elasticsearch_url,
        index_prefix=index_prefix,
        index_generation=index_generation,
        timeout_seconds=timeout_seconds,
    )
    return {
        "descriptor": {
            "encoder_name": VISUAL_SEARCH_V1_DESCRIPTOR.encoder_name,
            "encoder_revision": VISUAL_SEARCH_V1_DESCRIPTOR.encoder_revision,
            "embedding_schema_version": (
                VISUAL_SEARCH_V1_DESCRIPTOR.embedding_schema_version
            ),
            "dimension": VISUAL_SEARCH_V1_DESCRIPTOR.dimension,
            "preprocess_version": VISUAL_SEARCH_V1_DESCRIPTOR.preprocess_version,
            "similarity": VISUAL_SEARCH_V1_DESCRIPTOR.similarity,
        },
        "model": model,
        "elasticsearch": elasticsearch,
        "verified": bool(model["ready"] and elasticsearch["ready"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect read-only proof that the previous SigLIP v1 model snapshot "
            "and Elasticsearch namespace remain available for rollback."
        )
    )
    parser.add_argument(
        "--v1-model-path",
        type=Path,
        default=Path(
            "/var/lib/creative-asset-manager/models/siglip"
        )
        / SIGLIP_V1_REVISION,
    )
    parser.add_argument(
        "--elasticsearch-url",
        default="http://127.0.0.1:9200",
    )
    parser.add_argument("--index-prefix", default="creative-assets")
    parser.add_argument("--index-generation", default="v3")
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    import asyncio

    report = asyncio.run(
        collect_rollback_proof(
            v1_model_path=args.v1_model_path,
            elasticsearch_url=args.elasticsearch_url,
            index_prefix=args.index_prefix,
            index_generation=args.index_generation,
            timeout_seconds=args.timeout_seconds,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
