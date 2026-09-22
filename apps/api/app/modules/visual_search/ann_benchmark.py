from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.elasticsearch import (
    VisualSearchElasticsearchIndex,
    VisualSearchScope,
)
from app.modules.visual_search.model_spec import VISUAL_SEARCH_ACTIVE_DESCRIPTOR


@dataclass(frozen=True, slots=True)
class AnnBenchmarkProfile:
    name: str
    k: int
    num_candidates: int
    page_size: int


DEFAULT_PROFILES = (
    AnnBenchmarkProfile("A", 40, 160, 20),
    AnnBenchmarkProfile("B", 80, 240, 20),
    AnnBenchmarkProfile("C", 120, 320, 40),
    AnnBenchmarkProfile("D", 200, 500, 40),
)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("ANN benchmark dataset is unavailable or invalid") from exc
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases:
        raise RuntimeError("ANN benchmark dataset requires a non-empty cases array")
    return cases


def _embedding(case: dict[str, Any]) -> VisualEmbedding:
    values = case.get("embedding")
    if not isinstance(values, list):
        raise RuntimeError("ANN benchmark case requires an embedding array")
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ANN benchmark embedding must be numeric") from exc
    return VisualEmbedding(VISUAL_SEARCH_ACTIVE_DESCRIPTOR, vector)


def _expected(case: dict[str, Any]) -> set[str]:
    values = case.get("expected_asset_ids")
    if not isinstance(values, list) or not values:
        raise RuntimeError("ANN benchmark case requires expected_asset_ids")
    expected = {str(value).strip() for value in values if str(value).strip()}
    if not expected:
        raise RuntimeError("ANN benchmark expected_asset_ids cannot be empty")
    return expected


async def benchmark_ann(
    *,
    index: VisualSearchElasticsearchIndex,
    candidate_index: str,
    cases: Iterable[dict[str, Any]],
    profiles: tuple[AnnBenchmarkProfile, ...] = DEFAULT_PROFILES,
) -> dict[str, Any]:
    rows = list(cases)
    if not rows:
        raise ValueError("at least one ANN benchmark case is required")

    baseline: dict[str, list[str]] = {}
    baseline_latency: list[float] = []
    for position, case in enumerate(rows):
        tenant_id = str(case.get("tenant_id") or "").strip()
        case_id = str(case.get("id") or f"case-{position + 1}")
        embedding = _embedding(case)
        expected = _expected(case)
        scope = VisualSearchScope(tenant_id)
        started = perf_counter()
        hits = await index.search(
            embedding,
            scope=scope,
            limit=min(200, max(40, len(expected))),
            num_candidates=min(1000, max(160, len(expected) * 4)),
        )
        baseline_latency.append((perf_counter() - started) * 1000.0)
        baseline[case_id] = [hit.asset_id for hit in hits]

    profile_reports: list[dict[str, Any]] = []
    for profile in profiles:
        latencies: list[float] = []
        expected_recall: list[float] = []
        baseline_overlap: list[float] = []
        result_counts: list[int] = []
        case_rows: list[dict[str, Any]] = []

        for position, case in enumerate(rows):
            tenant_id = str(case.get("tenant_id") or "").strip()
            case_id = str(case.get("id") or f"case-{position + 1}")
            expected = _expected(case)
            embedding = _embedding(case)
            scope = VisualSearchScope(tenant_id)

            started = perf_counter()
            hits = await index.search_index(
                candidate_index,
                embedding,
                scope=scope,
                limit=profile.k,
                num_candidates=profile.num_candidates,
            )
            latency_ms = (perf_counter() - started) * 1000.0
            returned = [hit.asset_id for hit in hits]
            returned_set = set(returned)
            baseline_ids = baseline[case_id][: profile.k]
            baseline_set = set(baseline_ids)

            recall = len(expected & returned_set) / len(expected)
            overlap_denominator = max(1, len(baseline_set))
            overlap = len(baseline_set & returned_set) / overlap_denominator

            latencies.append(latency_ms)
            expected_recall.append(recall)
            baseline_overlap.append(overlap)
            result_counts.append(len(returned))
            case_rows.append(
                {
                    "id": case_id,
                    "expected_recall": recall,
                    "baseline_overlap": overlap,
                    "result_count": len(returned),
                    "latency_ms": latency_ms,
                }
            )

        profile_reports.append(
            {
                "name": profile.name,
                "k": profile.k,
                "num_candidates": profile.num_candidates,
                "page_size": profile.page_size,
                "latency_ms": {
                    "p50": statistics.median(latencies),
                    "p95": _percentile(latencies, 0.95),
                    "max": max(latencies),
                },
                "expected_recall": {
                    "mean": statistics.fmean(expected_recall),
                    "min": min(expected_recall),
                },
                "baseline_overlap": {
                    "mean": statistics.fmean(baseline_overlap),
                    "min": min(baseline_overlap),
                },
                "result_count": {
                    "mean": statistics.fmean(result_counts),
                    "min": min(result_counts),
                    "max": max(result_counts),
                },
                "cases": case_rows,
            }
        )

    return {
        "candidate_index": candidate_index,
        "embedding_schema_version": VISUAL_SEARCH_ACTIVE_DESCRIPTOR.embedding_schema_version,
        "case_count": len(rows),
        "baseline_latency_ms": {
            "p50": statistics.median(baseline_latency),
            "p95": _percentile(baseline_latency, 0.95),
            "max": max(baseline_latency),
        },
        "profiles": profile_reports,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    index = VisualSearchElasticsearchIndex(
        ElasticsearchV3Config(
            args.elasticsearch_url,
            index_prefix=args.index_prefix,
            index_generation=args.index_generation,
        ),
        VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
    )
    try:
        return await benchmark_ann(
            index=index,
            candidate_index=args.candidate_index,
            cases=_load_cases(args.cases),
        )
    finally:
        await index.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark a physical Visual Search ANN candidate against the active "
            "alias without switching aliases."
        )
    )
    parser.add_argument("--elasticsearch-url", required=True)
    parser.add_argument("--candidate-index", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--index-prefix", default="creative-assets")
    parser.add_argument("--index-generation", default="v3")
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
