from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.search.governance_model import SearchIndexRecordModel
from app.modules.search.metrics import SEARCH_V3_METRICS


@dataclass(frozen=True, slots=True)
class SearchV3Readiness:
    state: str
    search_available: bool
    failure_code: str | None
    message: str


class SearchV3ReadinessCache:
    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = Lock()
        self._values: dict[tuple[str, str], tuple[float, SearchV3Readiness]] = {}

    def get(
        self,
        key: tuple[str, str],
        *,
        ttl_seconds: float,
        loader: Callable[[], SearchV3Readiness],
    ) -> SearchV3Readiness:
        now = self._clock()
        with self._lock:
            cached = self._values.get(key)
            if cached is not None and cached[0] > now:
                result = cached[1]
            else:
                result = loader()
                self._values[key] = (now + max(1.0, min(ttl_seconds, 300.0)), result)
        SEARCH_V3_METRICS.observe_readiness(result.state)
        return result

    def invalidate(self, index_prefix: str | None = None) -> None:
        with self._lock:
            if index_prefix is None:
                self._values.clear()
                return
            self._values = {
                key: value for key, value in self._values.items() if key[0] != index_prefix
            }


SEARCH_V3_READINESS_CACHE = SearchV3ReadinessCache()


def load_search_v3_readiness(
    session: Session,
    *,
    index_prefix: str,
    expected_projection_version: str,
) -> SearchV3Readiness:
    rows = list(
        session.scalars(
            select(SearchIndexRecordModel)
            .where(
                SearchIndexRecordModel.index_prefix == index_prefix,
                SearchIndexRecordModel.lifecycle_state == "active",
            )
            .order_by(SearchIndexRecordModel.activated_at.desc().nullslast())
        )
    )
    if not rows:
        return SearchV3Readiness(
            "unavailable", False, "search_v3_governance_missing",
            "Search V3 has no adopted active index.",
        )
    if len(rows) != 1:
        return SearchV3Readiness(
            "incompatible", False, "search_v3_governance_ambiguous",
            "Search V3 governance has multiple active indexes.",
        )
    row = rows[0]
    verification = row.verification_json or {}
    if row.projection_version != expected_projection_version:
        return SearchV3Readiness(
            "incompatible", False, "search_v3_projection_mismatch",
            "Search V3 uses an incompatible projection version.",
        )
    required_true = (
        "alias_matches", "mapping_matches", "settings_match",
        "cursor_sort_matches", "projection_version_documents_match", "passed",
    )
    if all(verification.get(key) is True for key in required_true):
        return SearchV3Readiness("ready", True, None, "Search V3 is ready.")
    if any(verification.get(key) is False for key in required_true):
        return SearchV3Readiness(
            "incompatible", False, "search_v3_verification_failed",
            "Search V3 index verification failed.",
        )
    return SearchV3Readiness(
        "verification_unknown", False, "search_v3_verification_unknown",
        "Search V3 governance verification is incomplete.",
    )


def cached_search_v3_readiness(
    session: Session,
    *,
    index_prefix: str,
    expected_projection_version: str,
    ttl_seconds: float,
) -> SearchV3Readiness:
    return SEARCH_V3_READINESS_CACHE.get(
        (index_prefix, expected_projection_version),
        ttl_seconds=ttl_seconds,
        loader=lambda: load_search_v3_readiness(
            session,
            index_prefix=index_prefix,
            expected_projection_version=expected_projection_version,
        ),
    )
