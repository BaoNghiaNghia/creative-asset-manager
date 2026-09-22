from __future__ import annotations

import asyncio

from app.modules.visual_search.ann_benchmark import (
    AnnBenchmarkProfile,
    benchmark_ann,
)
from app.modules.visual_search.elasticsearch import VisualSearchHit
from app.modules.visual_search.model_spec import VISUAL_SEARCH_ACTIVE_DESCRIPTOR


def _embedding() -> list[float]:
    return [1.0] + [0.0] * (VISUAL_SEARCH_ACTIVE_DESCRIPTOR.dimension - 1)


class FakeIndex:
    def __init__(self) -> None:
        self.candidate_calls: list[tuple[str, int, int]] = []

    async def search(
        self,
        _embedding,
        *,
        scope,
        limit,
        num_candidates,
        **_kwargs,
    ):
        assert scope.tenant_id == "tenant-a"
        assert limit == 40
        assert num_candidates == 160
        return [
            VisualSearchHit(
                "base-1",
                "tenant-a",
                "asset-good",
                1.0,
                "a" * 64,
            ),
            VisualSearchHit(
                "base-2",
                "tenant-a",
                "asset-near",
                0.9,
                "b" * 64,
            ),
        ]

    async def search_index(
        self,
        target_index,
        _embedding,
        *,
        scope,
        limit,
        num_candidates,
        **_kwargs,
    ):
        assert scope.tenant_id == "tenant-a"
        self.candidate_calls.append(
            (target_index, limit, num_candidates)
        )
        return [
            VisualSearchHit(
                "candidate-1",
                "tenant-a",
                "asset-good",
                1.0,
                "a" * 64,
            ),
            VisualSearchHit(
                "candidate-2",
                "tenant-a",
                "asset-near",
                0.9,
                "b" * 64,
            ),
        ]


def test_ann_benchmark_reports_relevance_and_matrix_without_alias_mutation() -> None:
    async def verify() -> None:
        index = FakeIndex()
        profiles = (
            AnnBenchmarkProfile("A", 40, 160, 20),
            AnnBenchmarkProfile("B", 80, 240, 20),
        )
        report = await benchmark_ann(
            index=index,
            candidate_index=(
                "creative-assets-visual-visual_embedding_v2-v3-20260922-ann"
            ),
            cases=[
                {
                    "id": "case-a",
                    "tenant_id": "tenant-a",
                    "embedding": _embedding(),
                    "expected_asset_ids": ["asset-good"],
                }
            ],
            profiles=profiles,
        )

        assert report["case_count"] == 1
        assert [row["name"] for row in report["profiles"]] == ["A", "B"]
        for row in report["profiles"]:
            assert row["expected_recall"]["min"] == 1.0
            assert row["baseline_overlap"]["min"] == 1.0
            assert row["result_count"]["min"] == 2
        assert index.candidate_calls == [
            (
                "creative-assets-visual-visual_embedding_v2-v3-20260922-ann",
                40,
                160,
            ),
            (
                "creative-assets-visual-visual_embedding_v2-v3-20260922-ann",
                80,
                240,
            ),
        ]

    asyncio.run(verify())
