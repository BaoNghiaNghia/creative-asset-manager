"""Bounded, PII-free five-minute Etsy Spy timing and cache telemetry.

Only known static labels are recorded: never URLs, query strings, user IDs,
tenant identifiers, tokens, listing IDs or image hashes. Logs form durable
time-window summaries; Nginx-generated 502 responses cannot be observed here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from threading import Lock
from time import time

_LOG = logging.getLogger("app.etsy_spy_performance")
_PREFIX = "/api/v1/etsy-spy"
_FIXED_GET = {
    f"{_PREFIX}/summary": "summary",
    f"{_PREFIX}/listings": "listings",
    f"{_PREFIX}/listings/observed-days": "observed_days",
    f"{_PREFIX}/listings/observed-day-groups": "observed_day_groups",
    f"{_PREFIX}/image-agents/overview": "image_overview",
}
_THUMB_PATH = re.compile(
    r"^/api/v1/etsy-spy/image-agents/designs/[^/]+/[^/]+/thumbnail$"
)
_BOUNDS_MS = (50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000)


def route_label(method: str, path: str) -> str | None:
    if method != "GET":
        return None
    return _FIXED_GET.get(path) or (
        "design_thumbnail" if _THUMB_PATH.fullmatch(path) else None
    )


@dataclass
class _WindowStats:
    requests: int = 0
    errors_5xx: int = 0
    errors_502: int = 0
    histogram: list[int] = field(default_factory=lambda: [0] * (len(_BOUNDS_MS) + 1))
    cache_hits: int = 0
    cache_misses: int = 0
    coalesced_reads: int = 0


class EtsyPerformanceMeter:
    """Aggregates at most six labels and logs one line/label/5-minute window."""

    def __init__(self, *, window_seconds: int = 300) -> None:
        self.window_seconds = window_seconds
        self._lock = Lock()
        self._window: int | None = None
        self._rows: dict[str, _WindowStats] = {}

    def _advance(self, now: float) -> list[tuple[int, str, _WindowStats]]:
        window = int(now // self.window_seconds)
        if self._window is None:
            self._window = window
            return []
        if self._window == window:
            return []
        completed = [(self._window, label, entry) for label, entry in self._rows.items()]
        self._window = window
        self._rows = {}
        return completed

    @staticmethod
    def _publish(completed: list[tuple[int, str, _WindowStats]], duration: int) -> None:
        for window, label, row in completed:
            if row.requests == 0 and row.cache_hits == 0 and row.cache_misses == 0:
                continue
            # Histogram reports the *upper bound* containing the p95 sample.
            threshold = max(1, (row.requests * 95 + 99) // 100)
            accumulated = 0
            p95_upper = ">30000"
            for index, amount in enumerate(row.histogram):
                accumulated += amount
                if accumulated >= threshold:
                    p95_upper = str(_BOUNDS_MS[index]) if index < len(_BOUNDS_MS) else ">30000"
                    break
            _LOG.info(
                "etsy_perf_window window_start=%s window_s=%s route=%s requests=%s "
                "errors_5xx=%s errors_502=%s p95_le_ms=%s cache_hit=%s cache_miss=%s "
                "coalesced=%s histogram=%s",
                window * duration, duration, label, row.requests,
                row.errors_5xx, row.errors_502,
                p95_upper if row.requests else "no_requests",
                row.cache_hits, row.cache_misses, row.coalesced_reads,
                ",".join(map(str, row.histogram)),
            )

    def record(self, label: str, *, status_code: int, duration_ms: float,
               now: float | None = None) -> None:
        if label not in set(_FIXED_GET.values()) | {"design_thumbnail"}:
            return
        with self._lock:
            completed = self._advance(time() if now is None else now)
            row = self._rows.setdefault(label, _WindowStats())
            row.requests += 1
            row.errors_5xx += int(status_code >= 500)
            row.errors_502 += int(status_code == 502)
            bucket = next((idx for idx, bound in enumerate(_BOUNDS_MS)
                           if duration_ms <= bound), len(_BOUNDS_MS))
            row.histogram[bucket] += 1
        self._publish(completed, self.window_seconds)

    def cache(self, hit: bool, *, coalesced: bool = False,
              now: float | None = None) -> None:
        with self._lock:
            completed = self._advance(time() if now is None else now)
            row = self._rows.setdefault("design_thumbnail", _WindowStats())
            if hit:
                row.cache_hits += 1
            else:
                row.cache_misses += 1
            row.coalesced_reads += int(coalesced)
        self._publish(completed, self.window_seconds)


ETSY_PERFORMANCE = EtsyPerformanceMeter()
