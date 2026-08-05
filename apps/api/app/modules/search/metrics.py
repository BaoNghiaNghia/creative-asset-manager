from __future__ import annotations

from collections import Counter
from threading import Lock


class SearchV3Metrics:
    _READINESS_STATES = {"ready", "verification_unknown", "incompatible", "unavailable"}
    _ADOPTION_OUTCOMES = {
        "adopted", "already_active", "repaired", "dry_run_compatible",
        "incompatible", "alias_missing", "failed",
    }

    def __init__(self) -> None:
        self._lock = Lock()
        self._readiness: Counter[str] = Counter()
        self._adoption: Counter[str] = Counter()

    def observe_readiness(self, state: str) -> None:
        if state not in self._READINESS_STATES:
            raise ValueError("invalid Search V3 readiness state")
        with self._lock:
            self._readiness[state] += 1

    def observe_adoption(self, outcome: str) -> None:
        if outcome not in self._ADOPTION_OUTCOMES:
            raise ValueError("invalid Search V3 adoption outcome")
        with self._lock:
            self._adoption[outcome] += 1

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        with self._lock:
            return {
                "search_v3_readiness_total": [
                    {"state": state, "value": self._readiness[state]}
                    for state in sorted(self._READINESS_STATES)
                ],
                "search_v3_governance_adoption_total": [
                    {"outcome": outcome, "value": self._adoption[outcome]}
                    for outcome in sorted(self._ADOPTION_OUTCOMES)
                ],
            }

    def reset(self) -> None:
        with self._lock:
            self._readiness.clear()
            self._adoption.clear()


SEARCH_V3_METRICS = SearchV3Metrics()
