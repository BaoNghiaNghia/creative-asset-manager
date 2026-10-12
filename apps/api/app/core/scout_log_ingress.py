"""Low-overhead admission control for high-frequency Scout telemetry.

The guard runs before FastAPI opens a DB session. Excess telemetry receives a
real 429 with Retry-After so a client can keep its outbox; no fake ACK or
silent truncation. It never limits login, image generation or keyword searches.
"""
from __future__ import annotations

from threading import Lock
from time import monotonic

SCOUT_LOG_PATH_PREFIX = "/api/v1/realistic-review-ugc/scout-agents/"
SCOUT_LOG_SUFFIX = "/logs"
MAX_AGENTS_TRACKED = 256


def scout_log_agent_from_path(method: str, path: str) -> str | None:
    if method != "POST" or not path.startswith(SCOUT_LOG_PATH_PREFIX) or not path.endswith(SCOUT_LOG_SUFFIX):
        return None
    agent_id = path[len(SCOUT_LOG_PATH_PREFIX):-len(SCOUT_LOG_SUFFIX)]
    if len(agent_id) != 36 or agent_id.count("-") != 4:
        return None
    import uuid
    try:
        return str(uuid.UUID(agent_id))
    except ValueError:
        return None


class ScoutTelemetryLimiter:
    def __init__(self, *, rate: float = 2.0, burst: int = 8):
        self.rate = rate
        self.burst = burst
        self._lock = Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def admit(self, key: str, *, now: float | None = None) -> bool:
        timestamp = monotonic() if now is None else now
        with self._lock:
            if len(self._buckets) >= MAX_AGENTS_TRACKED and key not in self._buckets:
                # The agent registry is much smaller; expire the least-recently
                # used token bucket rather than letting arbitrary ids grow memory.
                oldest = min(self._buckets, key=lambda item: self._buckets[item][1])
                del self._buckets[oldest]
            tokens, last = self._buckets.get(key, (float(self.burst), timestamp))
            tokens = min(float(self.burst), tokens + max(0.0, timestamp - last) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, timestamp)
                return False
            self._buckets[key] = (tokens - 1.0, timestamp)
            return True
