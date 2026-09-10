from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from threading import Lock


class VisualSearchMetrics:
    """Thread-safe, process-local evidence over a bounded recent sample window."""

    _OUTCOMES = {"success", "empty", "error", "disabled", "unavailable", "skipped", "retired"}
    _KINDS = {"asset", "upload", "crop", "hybrid"}
    _QUERY_STAGES = {
        "request_total_ms", "prepare_image_ms", "encode_image_ms", "encode_text_ms",
        "knn_ms", "rank_ms", "hydrate_ms",
    }
    _INDEX_STAGES = {
        "job_total_ms", "source_read_ms", "prepare_image_ms", "encode_ms",
        "es_upsert_ms", "es_delete_ms",
    }

    def __init__(self, window_size: int = 512):
        if window_size < 1:
            raise ValueError("window_size must be positive")
        self._lock = Lock()
        self._window_size = window_size
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._requests: Counter[tuple[str, str]] = Counter()
        self._indexing: Counter[str] = Counter()
        self._samples: defaultdict[tuple[str, str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=window_size)
        )

    def _observe_stage(self, namespace: str, kind: str, stage: str, value: float) -> None:
        allowed = self._QUERY_STAGES if namespace == "query" else self._INDEX_STAGES
        if stage not in allowed:
            raise ValueError("invalid visual metric stage")
        self._samples[(namespace, kind, stage)].append(max(0.0, float(value)))

    def observe_request(self, *, kind: str, outcome: str, total_ms: float,
                        result_count: int, stages: dict[str, float] | None = None) -> None:
        if kind not in self._KINDS or outcome not in self._OUTCOMES:
            raise ValueError("invalid visual metric label")
        if outcome == "success" and result_count == 0:
            outcome = "empty"
        with self._lock:
            self._requests[(kind, outcome)] += 1
            self._observe_stage("query", kind, "request_total_ms", total_ms)
            for stage, value in (stages or {}).items():
                self._observe_stage("query", kind, stage, value)

    def observe_indexing(self, outcome: str,
                         stages: dict[str, float] | None = None) -> None:
        if outcome not in self._OUTCOMES:
            raise ValueError("invalid visual metric label")
        with self._lock:
            self._indexing[outcome] += 1
            for stage, value in (stages or {}).items():
                self._observe_stage("index", "index", stage, value)

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return ordered[index]

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            rows = []
            for (namespace, kind, stage), samples in sorted(self._samples.items()):
                values = list(samples)
                rows.append({
                    "kind": kind, "stage": stage, "sample_count": len(values),
                    "p50_ms": self._percentile(values, 0.50),
                    "p95_ms": self._percentile(values, 0.95),
                    "max_ms": max(values) if values else None, "namespace": namespace,
                })
            return {
                "process_started_at": self._started_at,
                "snapshot_generated_at": datetime.now(timezone.utc).isoformat(),
                "observation_window_size": self._window_size,
                "visual_search_requests_total": [
                    {"kind": kind, "outcome": outcome, "value": value}
                    for (kind, outcome), value in sorted(self._requests.items())
                ],
                "visual_index_jobs_total": [
                    {"outcome": outcome, "value": value}
                    for outcome, value in sorted(self._indexing.items())
                ],
                "query_latency": [row for row in rows if row["namespace"] == "query"],
                "index_latency": [row for row in rows if row["namespace"] == "index"],
            }


VISUAL_SEARCH_METRICS = VisualSearchMetrics()
