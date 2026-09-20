"""Safe process-local counters emitted as structured log events; DB is gauge authority."""
from __future__ import annotations
import logging
from collections import Counter
from threading import Lock

_ALLOWED = frozenset({
    "video_cache_fill_started_total",
    "video_cache_fill_completed_total",
    "video_cache_fill_failed_total",
    "video_cache_eviction_total",
    "video_cache_bypass_total",
})
_COUNTS: Counter[str] = Counter()
_LOCK = Lock()
_LOG = logging.getLogger("cam.video_cache")


def emit_counter(name: str) -> None:
    if name not in _ALLOWED:
        raise ValueError("Unknown video cache metric")
    with _LOCK:
        _COUNTS[name] += 1
    _LOG.info(name, extra={"metric_name": name, "metric_value": 1})


def counters_snapshot() -> dict[str, int]:
    with _LOCK:
        return dict(_COUNTS)
