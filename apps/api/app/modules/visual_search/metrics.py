from __future__ import annotations
from collections import Counter
from threading import Lock

class VisualSearchMetrics:
    _OUTCOMES = {"success", "empty", "error", "disabled", "unavailable", "skipped"}
    _KINDS = {"asset", "upload", "crop", "hybrid"}
    def __init__(self):
        self._lock = Lock()
        self._requests = Counter()
        self._indexing = Counter()
        self._latency_ms = Counter()
    def observe_request(self, *, kind: str, outcome: str, total_ms: int, result_count: int):
        if kind not in self._KINDS or outcome not in self._OUTCOMES: raise ValueError("invalid visual metric label")
        with self._lock:
            self._requests[(kind, outcome)] += 1
            self._latency_ms[kind] += max(0, total_ms)
            if result_count == 0: self._requests[(kind, "empty")] += 1
    def observe_indexing(self, outcome: str):
        if outcome not in self._OUTCOMES: raise ValueError("invalid visual metric label")
        with self._lock: self._indexing[outcome] += 1
    def snapshot(self):
        with self._lock:
            return {
                "visual_search_requests_total": [{"kind": k, "outcome": o, "value": self._requests[(k,o)]} for k,o in sorted(self._requests)],
                "visual_index_jobs_total": [{"outcome": o, "value": self._indexing[o]} for o in sorted(self._indexing)],
                "visual_search_total_duration_ms": [{"kind": k, "value": self._latency_ms[k]} for k in sorted(self._latency_ms)],
            }
VISUAL_SEARCH_METRICS = VisualSearchMetrics()
