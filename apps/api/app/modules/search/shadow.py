from __future__ import annotations
import asyncio
import hashlib
import re
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.modules.search.governance_model import SearchShadowObservationModel, TenantSearchShadowPolicyModel

SearchCall = Callable[[], Awaitable[Mapping[str, Any]]]
_SENSITIVE = re.compile(r"(https?://|[\w.+-]+@[\w.-]+|bearer\s+|api[_-]?key|token=|[A-Za-z0-9_-]{48,})", re.I)
_ERROR_CATEGORIES = frozenset({"timeout", "cancelled", "authentication", "unavailable", "invalid_response", "provider_error"})
_SURFACES = frozenset({"explorer_search", "explorer_search_stream", "search_v2"})

@dataclass(frozen=True, slots=True)
class EffectiveShadowPolicy:
    enabled: bool
    primary_version: str
    shadow_version: str
    sample_percentage: int
    timeout_ms: int
    persist_raw_query: bool
    top_k: int

class SearchShadowMetrics:
    """Bounded metrics: labels never contain tenant, query, asset, or request IDs."""
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = Counter()
        self._latencies = Counter()

    def observe(self, *, surface, primary_version, shadow_version, outcome, primary_ms, shadow_ms):
        surface = surface if surface in _SURFACES else "unknown"
        outcome = outcome if outcome in _ERROR_CATEGORIES | {"success", "not_sampled", "disabled", "direction_mismatch", "policy_error"} else "provider_error"
        primary_version = primary_version if primary_version in {"v1", "v2"} else "unknown"
        shadow_version = shadow_version if shadow_version in {"v1", "v2"} else "unknown"
        with self._lock:
            self._counts[(surface, primary_version, shadow_version, outcome)] += 1
            self._latencies[(surface, "primary", outcome)] += max(0, primary_ms)
            if shadow_ms is not None:
                self._latencies[(surface, "shadow", outcome)] += max(0, shadow_ms)

    def snapshot(self):
        with self._lock:
            return {
                "counts": [{"surface": k[0], "primary_version": k[1], "shadow_version": k[2], "outcome": k[3], "value": v} for k, v in sorted(self._counts.items())],
                "latency_sum_ms": [{"surface": k[0], "role": k[1], "outcome": k[2], "value": v} for k, v in sorted(self._latencies.items())],
            }

SEARCH_SHADOW_METRICS = SearchShadowMetrics()

class SearchShadowRepository:
    def __init__(self, session: Session):
        self.session = session

    def effective_policy(self, tenant_id, *, global_enabled, max_timeout_ms):
        row = self.session.get(TenantSearchShadowPolicyModel, tenant_id)
        if row is None:
            return EffectiveShadowPolicy(False, "v1", "v2", 0, min(250, max_timeout_ms), False, 10)
        return EffectiveShadowPolicy(
            bool(global_enabled and row.enabled and not row.emergency_disabled),
            row.primary_version, row.shadow_version,
            max(0, min(100, row.sample_percentage)),
            max(1, min(row.timeout_ms, max_timeout_ms)),
            bool(row.persist_raw_query), max(1, min(100, row.top_k)),
        )

    def save(self, observation):
        self.session.add(observation)
        self.session.commit()

    def report(self, tenant_id, *, started_at=None, ended_at=None, query_type=None,
               metadata_profile=None, primary_version=None, shadow_version=None,
               error_category=None):
        conditions = [SearchShadowObservationModel.tenant_id == tenant_id]
        filters = {
            "started_at": started_at, "ended_at": ended_at, "query_type": query_type,
            "metadata_profile": metadata_profile, "primary_version": primary_version,
            "shadow_version": shadow_version, "error_category": error_category,
        }
        if started_at:
            conditions.append(SearchShadowObservationModel.occurred_at >= started_at)
        if ended_at:
            conditions.append(SearchShadowObservationModel.occurred_at < ended_at)
        for value, column in (
            (query_type, SearchShadowObservationModel.query_type),
            (metadata_profile, SearchShadowObservationModel.metadata_profile),
            (primary_version, SearchShadowObservationModel.primary_version),
            (shadow_version, SearchShadowObservationModel.shadow_version),
            (error_category, SearchShadowObservationModel.error_category),
        ):
            if value is not None:
                conditions.append(column == value)
        rows = list(self.session.scalars(select(SearchShadowObservationModel).where(*conditions).order_by(SearchShadowObservationModel.occurred_at, SearchShadowObservationModel.id)))
        successful = [row for row in rows if row.error_category is None]
        overlaps = [row.top_k_overlap for row in successful if row.top_k_overlap is not None]
        primary_latency = [row.primary_latency_ms for row in rows]
        shadow_latency = [row.shadow_latency_ms for row in rows if row.shadow_latency_ms is not None]
        differences = [row.shadow_count - row.primary_count for row in successful if row.shadow_count is not None]
        errors = Counter(row.error_category for row in rows if row.error_category)
        top_one = [row.top_result_agrees for row in successful if row.top_result_agrees is not None]
        zero = [row.zero_result_disagrees for row in successful if row.zero_result_disagrees is not None]
        return {
            "tenant_id": tenant_id, "filters": filters, "observations": len(rows),
            "successful_comparisons": len(successful), "shadow_errors": sum(errors.values()),
            "error_categories": dict(sorted(errors.items())),
            "top_k_overlap_formula": "|unique(primary[:K]) intersect unique(shadow[:K])| / K",
            "average_top_k_overlap": _average(overlaps),
            "top_1_agreement_rate": _rate(top_one),
            "zero_result_difference_count": sum(bool(v) for v in zero),
            "zero_result_difference_rate": _rate(zero),
            "average_result_count_difference": _average(differences),
            "average_absolute_result_count_difference": _average([abs(v) for v in differences]),
            "primary_latency_ms": _distribution(primary_latency),
            "shadow_latency_ms": _distribution(shadow_latency),
        }

def _average(values):
    return float(sum(values) / len(values)) if values else 0.0

def _rate(values):
    return float(sum(bool(value) for value in values) / len(values)) if values else 0.0

def _distribution(values):
    ordered = sorted(max(0, int(value)) for value in values)
    return {"count": len(ordered), "average": _average(ordered), "p50": _percentile(ordered, .5), "p95": _percentile(ordered, .95)}

def _percentile(ordered, quantile):
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))

class SearchShadowComparator:
    def __init__(self, *, session_factory, global_enabled, max_timeout_ms, metrics=None):
        self.session_factory = session_factory
        self.global_enabled = global_enabled
        self.max_timeout_ms = max(1, max_timeout_ms)
        self.metrics = metrics or SEARCH_SHADOW_METRICS
        self._tasks = set()
        self._provider_tasks = set()
        self._accepting = True

    def start(self):
        self._accepting = True

    async def execute(self, *, tenant_id, query, primary, shadow, primary_version="v1",
                      shadow_version="v2", surface="explorer_search", metadata_profile=None):
        started = time.perf_counter()
        primary_result = await primary()
        primary_ms = int((time.perf_counter() - started) * 1000)
        await self.observe(
            tenant_id=tenant_id, query=query, primary_result=primary_result,
            primary_ms=primary_ms, shadow=shadow, primary_version=primary_version,
            shadow_version=shadow_version, surface=surface,
            metadata_profile=metadata_profile,
        )
        return primary_result

    async def observe(self, *, tenant_id, query, primary_result, primary_ms, shadow,
                      primary_version, shadow_version, surface, metadata_profile=None):
        if not self._accepting:
            return False
        try:
            with self.session_factory() as session:
                policy = SearchShadowRepository(session).effective_policy(
                    tenant_id, global_enabled=self.global_enabled(),
                    max_timeout_ms=self.max_timeout_ms,
                )
        except Exception:
            self.metrics.observe(surface=surface, primary_version=primary_version, shadow_version=shadow_version, outcome="policy_error", primary_ms=primary_ms, shadow_ms=None)
            return False
        outcome = None
        if not policy.enabled:
            outcome = "disabled"
        elif policy.primary_version != primary_version or policy.shadow_version != shadow_version:
            outcome = "direction_mismatch"
        elif not self._sampled(tenant_id, query, surface, policy.sample_percentage):
            outcome = "not_sampled"
        if outcome:
            self.metrics.observe(surface=surface, primary_version=primary_version, shadow_version=shadow_version, outcome=outcome, primary_ms=primary_ms, shadow_ms=None)
            return False
        task = asyncio.create_task(self._observe(
            tenant_id=tenant_id, query=query, policy=policy,
            primary_result=primary_result, primary_ms=primary_ms, shadow=shadow,
            metadata_profile=metadata_profile, surface=surface,
        ))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return True

    def _task_done(self, task):
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def drain(self, timeout_seconds=None):
        tracked = tuple(self._tasks | self._provider_tasks)
        if not tracked:
            return
        if timeout_seconds is None:
            await asyncio.gather(*tracked, return_exceptions=True)
            return
        done, pending = await asyncio.wait(tracked, timeout=max(0, timeout_seconds))
        for task in pending:
            task.cancel()
        if pending:
            # Cancellation is best effort: providers that suppress it are detached.
            await asyncio.sleep(0)
        for task in done:
            if not task.cancelled():
                task.exception()

    async def shutdown(self, timeout_seconds):
        self._accepting = False
        await self.drain(timeout_seconds)

    @staticmethod
    def _sampled(tenant_id, query, surface, percentage):
        digest = hashlib.sha256(f"{tenant_id}\0{surface}\0{query}".encode()).hexdigest()
        return int(digest[:8], 16) % 10_000 < max(0, min(100, percentage)) * 100

    async def _observe(self, *, tenant_id, query, policy, primary_result, primary_ms,
                       shadow, metadata_profile, surface):
        started = time.perf_counter()
        error = None
        shadow_result = {}
        provider_task = asyncio.create_task(shadow())
        self._provider_tasks.add(provider_task)
        provider_task.add_done_callback(self._provider_done)
        try:
            done, _ = await asyncio.wait({provider_task}, timeout=policy.timeout_ms / 1000)
            if not done:
                error = "timeout"
                provider_task.cancel()
            else:
                value = provider_task.result()
                if not isinstance(value, Mapping):
                    error = "invalid_response"
                else:
                    shadow_result = value
        except asyncio.CancelledError:
            provider_task.cancel()
            raise
        except Exception as exc:
            error = self._error_category(exc)
        shadow_ms = min(policy.timeout_ms, int((time.perf_counter() - started) * 1000))
        primary_ids = self._ids(primary_result)[:policy.top_k]
        shadow_ids = self._ids(shadow_result)[:policy.top_k]
        observation = SearchShadowObservationModel(
            tenant_id=tenant_id, query_hash=hashlib.sha256(query.encode()).hexdigest(),
            raw_query=query if policy.persist_raw_query and not _SENSITIVE.search(query) else None,
            query_type=self._query_type(query),
            query_features_json={"terms": min(100, len(query.split())), "quoted": '"' in query, "qualified": ":" in query, "or": " OR " in query.upper(), "surface": surface if surface in _SURFACES else "unknown"},
            metadata_profile=metadata_profile, primary_version=policy.primary_version,
            shadow_version=policy.shadow_version, primary_latency_ms=max(0, primary_ms),
            shadow_latency_ms=shadow_ms, primary_count=self._count(primary_result),
            shadow_count=None if error else self._count(shadow_result),
            top_k_overlap=len(set(primary_ids) & set(shadow_ids)) / policy.top_k if error is None else None,
            rank_correlation=self._rank_score(primary_ids, shadow_ids) if error is None else None,
            top_result_agrees=(primary_ids[0] == shadow_ids[0]) if error is None and primary_ids and shadow_ids else None,
            zero_result_disagrees=(self._count(primary_result) == 0) != (self._count(shadow_result) == 0) if error is None else None,
            error_category=error,
        )
        try:
            with self.session_factory() as session:
                SearchShadowRepository(session).save(observation)
        except Exception:
            self.metrics.observe(surface=surface, primary_version=policy.primary_version, shadow_version=policy.shadow_version, outcome="unavailable", primary_ms=primary_ms, shadow_ms=shadow_ms)
            return
        self.metrics.observe(surface=surface, primary_version=policy.primary_version, shadow_version=policy.shadow_version, outcome=error or "success", primary_ms=primary_ms, shadow_ms=shadow_ms)

    def _provider_done(self, task):
        self._provider_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _error_category(exc):
        if isinstance(exc, asyncio.TimeoutError):
            return "timeout"
        if isinstance(exc, PermissionError):
            return "authentication"
        if isinstance(exc, (ConnectionError, OSError)):
            return "unavailable"
        if isinstance(exc, (TypeError, ValueError)):
            return "invalid_response"
        return "provider_error"

    @staticmethod
    def _ids(result):
        items = result.get("items") or result.get("hits") or []
        if isinstance(items, Mapping):
            items = items.get("hits") or []
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            return []
        output = []
        for item in items:
            if isinstance(item, Mapping):
                value = item.get("internal_asset_id") or item.get("asset_id") or item.get("_id") or item.get("id")
                if value:
                    output.append(str(value))
        return output

    @staticmethod
    def _count(result):
        value = result.get("total")
        if isinstance(value, Mapping):
            value = value.get("value")
        return int(value if isinstance(value, (int, float)) else len(SearchShadowComparator._ids(result)))

    @staticmethod
    def _rank_score(primary, shadow):
        shared = set(primary) & set(shadow)
        if not shared:
            return 0.0
        distance = sum(abs(primary.index(item) - shadow.index(item)) for item in shared)
        return max(0.0, 1.0 - distance / max(1, len(shared) * max(len(primary), len(shadow))))

    @staticmethod
    def _query_type(query):
        if " OR " in query.upper():
            return "or"
        if "," in query:
            return "strict_and"
        if '"' in query:
            return "phrase"
        if ":" in query:
            return "qualified"
        return "soft_and" if len(query.split()) > 1 else "term"
