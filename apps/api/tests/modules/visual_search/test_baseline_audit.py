from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx

from app.modules.visual_search.baseline_audit import (
    _diagnostic_summary,
    collect_baseline_audit,
)


def test_baseline_audit_captures_sanitized_read_only_state(
    tmp_path: Path,
) -> None:
    diagnostics = tmp_path / "diagnostics.json"
    diagnostics.write_text(
        json.dumps(
            {
                "enabled": True,
                "upload_enabled": True,
                "crop_enabled": True,
                "hybrid_text_enabled": True,
                "backfill_enabled": False,
                "metrics": {
                    "query_latency": [
                        {
                            "kind": "upload",
                            "stage": stage,
                            "sample_count": 10,
                            "p50_ms": 50.0,
                            "p95_ms": 100.0,
                            "max_ms": 120.0,
                        }
                        for stage in (
                            "request_total_ms",
                            "encode_image_ms",
                            "encode_text_ms",
                            "knn_ms",
                        )
                    ]
                },
                "secret": "must-not-propagate",
            }
        ),
        encoding="utf-8",
    )

    index = "creative-assets-visual-visual_embedding_v1-v3-20260901"
    alias = "creative-assets-visual-visual_embedding_v1-v3-read"

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "encoder.test" and path == "/ready":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "runtime": "transformers",
                    "schema": "visual_embedding_v1",
                    "dimension": 768,
                },
            )
        if host != "elasticsearch.test":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(
                200,
                json={"version": {"number": "8.15.3"}},
            )
        if path == "/_cluster/health":
            return httpx.Response(
                200,
                json={
                    "status": "green",
                    "number_of_nodes": 1,
                    "active_shards": 3,
                    "unassigned_shards": 0,
                },
            )
        if path == "/_cluster/stats":
            return httpx.Response(
                200,
                json={
                    "indices": {
                        "store": {"size_in_bytes": 1234}
                    },
                    "nodes": {
                        "fs": {
                            "total_in_bytes": 30_000,
                            "available_in_bytes": 15_000,
                        },
                        "jvm": {
                            "mem": {
                                "heap_used_in_bytes": 1000,
                                "heap_max_in_bytes": 4000,
                            }
                        },
                    },
                },
            )
        if path.startswith("/_alias/creative-assets-visual-"):
            return httpx.Response(
                200,
                json={
                    index: {
                        "aliases": {
                            alias: {},
                        }
                    }
                },
            )
        if path == f"/{index}/_mapping":
            return httpx.Response(
                200,
                json={
                    index: {
                        "mappings": {
                            "properties": {
                                "visual_embedding": {
                                    "type": "dense_vector",
                                    "dims": 768,
                                    "index": True,
                                    "similarity": "cosine",
                                }
                            }
                        }
                    }
                },
            )
        if path == f"/{index}/_count":
            return httpx.Response(200, json={"count": 42})
        return httpx.Response(404)

    host_snapshot = {
        "cpu_count": 3,
        "memory_bytes": {
            "total": 8 * 1024**3,
            "available": 3 * 1024**3,
        },
        "swap_bytes": {"total": 2 * 1024**3, "used": 0},
        "root_disk_bytes": {
            "total": 30 * 1024**3,
            "used": 10 * 1024**3,
            "free": 20 * 1024**3,
        },
        "load_average": {"1m": 0.1, "5m": 0.2, "15m": 0.3},
    }

    with patch(
        "app.modules.visual_search.baseline_audit._host_snapshot",
        return_value=host_snapshot,
    ), patch(
        "app.modules.visual_search.baseline_audit._process_rss_bytes",
        return_value=900_000_000,
    ):
        report = collect_baseline_audit(
            encoder_ready_url="http://encoder.test/ready",
            elasticsearch_url="http://elasticsearch.test",
            deployed_commit="abc123",
            diagnostics_report=diagnostics,
            encoder_pid=123,
            transport=httpx.MockTransport(handler),
        )

    assert report["complete"] is True
    assert report["deployed_commit"] == "abc123"
    assert report["encoder"]["rss_bytes"] == 900_000_000
    assert report["elasticsearch"]["version"] == "8.15.3"
    assert report["elasticsearch"]["visual"]["aliases"] == [alias]
    assert report["elasticsearch"]["visual"]["indices"][0][
        "visual_embedding"
    ]["dims"] == 768
    assert report["visual_search_diagnostics"]["metrics"][
        "query_latency"
    ][0]["p95_ms"] == 100.0
    assert "secret" not in report["visual_search_diagnostics"]


def test_diagnostics_without_latency_samples_fail_closed() -> None:
    summary = _diagnostic_summary(
        {
            "enabled": True,
            "upload_enabled": True,
            "crop_enabled": True,
            "hybrid_text_enabled": True,
            "backfill_enabled": False,
            "metrics": {},
        }
    )

    assert summary is not None
    coverage = summary["baseline_stage_coverage"]
    assert coverage["complete"] is False
    assert coverage["observed"] == []
    assert coverage["missing"] == [
        "encode_image_ms",
        "encode_text_ms",
        "knn_ms",
        "request_total_ms",
    ]
