"""Safe process-local video-cache and delivery metrics emitted as structured logs.

PostgreSQL remains authoritative for durable cache state. Delivery counters,
latency samples and circuit-breaker state are intentionally process-local
rollout evidence; they contain no tenant IDs, asset IDs, URLs, keys or secrets.
"""
from __future__ import annotations

import logging
import math
from collections import Counter, deque
from threading import Lock

_ALLOWED = frozenset({
    "video_cache_fill_started_total",
    "video_cache_fill_completed_total",
    "video_cache_fill_failed_total",
    "video_cache_eviction_total",
    "video_cache_bypass_total",
    "video_cdn_redirect_total",
    "video_cdn_fallback_runtime_total",
    "video_cdn_fallback_rollout_scope_total",
    "video_cdn_fallback_cache_miss_total",
    "video_cdn_fallback_cache_identity_total",
    "video_cdn_fallback_guard_total",
    "video_cdn_fallback_delivery_error_total",
    "video_cdn_fallback_internal_total",
    "video_cdn_probe_success_total",
    "video_cdn_probe_failure_total",
    "video_cdn_guard_open_total",
})
_DELIVERY_COUNTERS = tuple(sorted(name for name in _ALLOWED if name.startswith("video_cdn_")))
_COUNTS: Counter[str] = Counter()
_DELIVERY_LATENCY_MS: deque[float] = deque(maxlen=512)
_LOCK = Lock()
_LOG = logging.getLogger("cam.video_cache")


def emit_counter(name: str) -> None:
    if name not in _ALLOWED:
        raise ValueError("Unknown video cache metric")
    with _LOCK:
        _COUNTS[name] += 1
    _LOG.info(name, extra={"metric_name": name, "metric_value": 1})


def observe_delivery_decision_ms(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid video delivery latency")
    numeric = float(value)
    with _LOCK:
        _DELIVERY_LATENCY_MS.append(numeric)
    _LOG.info(
        "video_cdn_decision_latency_ms",
        extra={"metric_name": "video_cdn_decision_latency_ms", "metric_value": numeric},
    )


def counters_snapshot() -> dict[str, int]:
    with _LOCK:
        return dict(_COUNTS)


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * ratio) - 1)
    return round(ordered[index], 3)


def delivery_observability_snapshot() -> dict[str, object]:
    with _LOCK:
        counters = {name: int(_COUNTS.get(name, 0)) for name in _DELIVERY_COUNTERS}
        latency = list(_DELIVERY_LATENCY_MS)
    return {
        "scope": "process_local",
        "counters": counters,
        "decision_latency_ms": {
            "sample_count": len(latency),
            "p50": _percentile(latency, 0.50),
            "p95": _percentile(latency, 0.95),
            "max": round(max(latency), 3) if latency else None,
        },
    }


def reset_delivery_observability_for_test() -> None:
    with _LOCK:
        for name in _DELIVERY_COUNTERS:
            _COUNTS.pop(name, None)
        _DELIVERY_LATENCY_MS.clear()
