from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from app.modules.visual_search.release_evidence import (
    _elasticsearch_snapshot,
    _host_snapshot,
    _process_rss_bytes,
    _read_json,
)


def _vector_mapping(
    payload: Mapping[str, Any],
    index_name: str,
) -> dict[str, Any] | None:
    index = payload.get(index_name)
    if not isinstance(index, Mapping):
        return None
    mappings = index.get("mappings")
    if not isinstance(mappings, Mapping):
        return None
    properties = mappings.get("properties")
    if not isinstance(properties, Mapping):
        return None
    vector = properties.get("visual_embedding")
    if not isinstance(vector, Mapping):
        return None
    allowed = {
        "type",
        "dims",
        "index",
        "similarity",
        "index_options",
    }
    return {
        key: value
        for key, value in vector.items()
        if key in allowed
    }


def _visual_alias_snapshot(
    client: httpx.Client,
    elasticsearch_url: str,
    index_prefix: str,
) -> dict[str, Any]:
    pattern = f"{index_prefix}-visual-*"
    response = client.get(
        elasticsearch_url.rstrip("/") + f"/_alias/{pattern}"
    )
    if response.status_code == 404:
        return {
            "pattern": pattern,
            "aliases": [],
            "indices": [],
        }
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Elasticsearch visual alias response is malformed")

    aliases: set[str] = set()
    indices: list[dict[str, Any]] = []
    for index_name, index_value in sorted(payload.items()):
        if not isinstance(index_value, Mapping):
            continue
        alias_value = index_value.get("aliases")
        index_aliases = (
            sorted(str(name) for name in alias_value)
            if isinstance(alias_value, Mapping)
            else []
        )
        aliases.update(index_aliases)

        mapping_response = client.get(
            elasticsearch_url.rstrip("/") + f"/{index_name}/_mapping"
        )
        mapping_response.raise_for_status()
        mapping_payload = mapping_response.json()
        if not isinstance(mapping_payload, dict):
            raise RuntimeError(
                "Elasticsearch visual mapping response is malformed"
            )
        count_response = client.get(
            elasticsearch_url.rstrip("/") + f"/{index_name}/_count"
        )
        count_response.raise_for_status()
        count_payload = count_response.json()
        if not isinstance(count_payload, dict):
            raise RuntimeError(
                "Elasticsearch visual count response is malformed"
            )
        indices.append(
            {
                "name": index_name,
                "aliases": index_aliases,
                "document_count": count_payload.get("count"),
                "visual_embedding": _vector_mapping(
                    mapping_payload,
                    index_name,
                ),
            }
        )

    return {
        "pattern": pattern,
        "aliases": sorted(aliases),
        "indices": indices,
    }


def _diagnostic_summary(
    diagnostics: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if diagnostics is None:
        return None
    metrics = diagnostics.get("metrics")
    if not isinstance(metrics, dict):
        raise RuntimeError("Visual Search diagnostics report is malformed")
    latency = metrics.get("query_latency")
    latency_rows = latency if isinstance(latency, list) else []
    observed_stages = {
        str(row.get("stage"))
        for row in latency_rows
        if isinstance(row, dict)
        and isinstance(row.get("sample_count"), int)
        and int(row["sample_count"]) > 0
    }
    required_stages = {
        "request_total_ms",
        "encode_image_ms",
        "knn_ms",
    }
    if diagnostics.get("hybrid_text_enabled") is True:
        required_stages.add("encode_text_ms")
    missing_stages = sorted(required_stages - observed_stages)
    return {
        "enabled": diagnostics.get("enabled"),
        "upload_enabled": diagnostics.get("upload_enabled"),
        "crop_enabled": diagnostics.get("crop_enabled"),
        "hybrid_text_enabled": diagnostics.get("hybrid_text_enabled"),
        "backfill_enabled": diagnostics.get("backfill_enabled"),
        "metrics": metrics,
        "baseline_stage_coverage": {
            "required": sorted(required_stages),
            "observed": sorted(observed_stages),
            "missing": missing_stages,
            "complete": not missing_stages,
        },
    }


def collect_baseline_audit(
    *,
    encoder_ready_url: str,
    elasticsearch_url: str,
    deployed_commit: str | None,
    diagnostics_report: Path | None = None,
    encoder_pid: int | None = None,
    index_prefix: str = "creative-assets",
    timeout_seconds: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    diagnostics = _diagnostic_summary(
        _read_json(diagnostics_report)
    )
    with httpx.Client(
        timeout=timeout_seconds,
        transport=transport,
    ) as client:
        encoder_response = client.get(encoder_ready_url)
        encoder_response.raise_for_status()
        encoder = encoder_response.json()
        if not isinstance(encoder, dict):
            raise RuntimeError("encoder ready response is malformed")
        elasticsearch = _elasticsearch_snapshot(
            client,
            elasticsearch_url,
        )
        visual_indices = _visual_alias_snapshot(
            client,
            elasticsearch_url,
            index_prefix,
        )

    host = _host_snapshot()
    commit = (deployed_commit or "").strip() or None
    bundle: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "deployed_commit": commit,
        "host": host,
        "encoder": {
            **encoder,
            "rss_bytes": _process_rss_bytes(encoder_pid),
        },
        "elasticsearch": {
            **elasticsearch,
            "visual": visual_indices,
        },
        "visual_search_diagnostics": diagnostics,
    }
    bundle["complete"] = bool(
        commit
        and encoder.get("status") == "ok"
        and elasticsearch.get("version")
        and visual_indices["aliases"]
        and int(
            host.get("memory_bytes", {}).get("total", 0)
            if isinstance(host.get("memory_bytes"), dict)
            else 0
        )
        > 0
        and diagnostics is not None
        and diagnostics.get("baseline_stage_coverage", {}).get("complete")
        is True
    )
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a read-only Visual Search CPU baseline before rollout. "
            "This command does not mutate indices, flags, models, or services."
        )
    )
    parser.add_argument(
        "--encoder-ready-url",
        default="http://127.0.0.1:8091/ready",
    )
    parser.add_argument(
        "--elasticsearch-url",
        default="http://127.0.0.1:9200",
    )
    parser.add_argument(
        "--deployed-commit",
        default=os.environ.get("CAM_DEPLOYED_COMMIT", ""),
    )
    parser.add_argument(
        "--diagnostics-report",
        type=Path,
        help=(
            "JSON exported from the authenticated Visual Search diagnostics "
            "endpoint; raw auth/session material is never written to this audit."
        ),
    )
    parser.add_argument("--encoder-pid", type=int)
    parser.add_argument("--index-prefix", default="creative-assets")
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = collect_baseline_audit(
        encoder_ready_url=args.encoder_ready_url,
        elasticsearch_url=args.elasticsearch_url,
        deployed_commit=args.deployed_commit,
        diagnostics_report=args.diagnostics_report,
        encoder_pid=args.encoder_pid,
        index_prefix=args.index_prefix,
        timeout_seconds=args.timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
