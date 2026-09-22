from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.modules.visual_search.model_spec import (
    SIGLIP_V1_REVISION,
    VISUAL_SEARCH_V1_DESCRIPTOR,
)
from app.modules.visual_search.rollback_proof import (
    inspect_v1_elasticsearch,
    inspect_v1_model_snapshot,
)


def test_v1_model_snapshot_requires_exact_revision_and_core_files(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / SIGLIP_V1_REVISION
    snapshot.mkdir()
    for name in (
        "config.json",
        "model.safetensors",
        "preprocessor_config.json",
    ):
        (snapshot / name).write_bytes(name.encode("utf-8"))

    report = inspect_v1_model_snapshot(snapshot)

    assert report["ready"] is True
    assert report["revision_path_matches"] is True
    assert report["missing_core_files"] == []
    assert set(report["core_files"]) == {
        "config.json",
        "model.safetensors",
        "preprocessor_config.json",
    }
    assert all(
        len(item["sha256"]) == 64
        for item in report["core_files"].values()
    )


def test_v1_elasticsearch_proof_checks_alias_mapping_and_count() -> None:
    async def verify() -> None:
        alias = (
            "creative-assets-visual-visual_embedding_v1-v3-read"
        )
        index = (
            "creative-assets-visual-visual_embedding_v1-v3-20260901"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == f"/_alias/{alias}":
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
                                        "dims": VISUAL_SEARCH_V1_DESCRIPTOR.dimension,
                                        "index": True,
                                        "similarity": VISUAL_SEARCH_V1_DESCRIPTOR.similarity,
                                    }
                                }
                            }
                        }
                    },
                )
            if path == f"/{index}/_count":
                return httpx.Response(200, json={"count": 42})
            return httpx.Response(404, json={"error": "not found"})

        report = await inspect_v1_elasticsearch(
            elasticsearch_url="http://elasticsearch.test",
            index_prefix="creative-assets",
            index_generation="v3",
            timeout_seconds=1.0,
            transport=httpx.MockTransport(handler),
        )

        assert report["ready"] is True
        assert report["target_indices"] == [index]
        assert report["indices"][0]["document_count"] == 42
        assert report["indices"][0]["compatible"] is True

    asyncio.run(verify())


def test_v1_elasticsearch_proof_fails_closed_when_alias_is_missing() -> None:
    async def verify() -> None:
        report = await inspect_v1_elasticsearch(
            elasticsearch_url="http://elasticsearch.test",
            index_prefix="creative-assets",
            index_generation="v3",
            timeout_seconds=1.0,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    404,
                    json={"error": "alias missing"},
                )
            ),
        )
        assert report["ready"] is False
        assert report["target_indices"] == []

    asyncio.run(verify())
