from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.search.governance_model import SearchShadowObservationModel, TenantSearchShadowPolicyModel

SearchCall = Callable[[], Awaitable[Mapping[str, Any]]]
_SENSITIVE = re.compile(r"(https?://|[\w.+-]+@[\w.-]+|bearer\s+|api[_-]?key|token=|[A-Za-z0-9_-]{48,})", re.I)


@dataclass(frozen=True, slots=True)
class EffectiveShadowPolicy:
    enabled: bool
    primary_version: str
    shadow_version: str
    sample_percentage: int
    timeout_ms: int
    persist_raw_query: bool
    top_k: int


class SearchShadowRepository:
    def __init__(self, session: Session):
        self.session = session

    def effective_policy(self, tenant_id: str, *, global_enabled: bool, max_timeout_ms: int) -> EffectiveShadowPolicy:
        row = self.session.get(TenantSearchShadowPolicyModel, tenant_id)
        if row is None:
            return EffectiveShadowPolicy(False, "v1", "v2", 0, min(250, max_timeout_ms), False, 10)
        return EffectiveShadowPolicy(
            bool(global_enabled and row.enabled and not row.emergency_disabled),
            row.primary_version, row.shadow_version, row.sample_percentage,
            min(row.timeout_ms, max_timeout_ms), row.persist_raw_query, row.top_k,
        )

    def save(self, observation: SearchShadowObservationModel) -> None:
        self.session.add(observation)
        self.session.commit()

    def report(self, tenant_id: str, *, started_at: datetime | None = None, ended_at: datetime | None = None, query_type: str | None = None) -> dict[str, Any]:
        conditions = [SearchShadowObservationModel.tenant_id == tenant_id]
        if started_at:
            conditions.append(SearchShadowObservationModel.occurred_at >= started_at)
        if ended_at:
            conditions.append(SearchShadowObservationModel.occurred_at < ended_at)
        if query_type:
            conditions.append(SearchShadowObservationModel.query_type == query_type)
        count, overlap, latency, errors = self.session.execute(
            select(
                func.count(), func.avg(SearchShadowObservationModel.top_k_overlap),
                func.avg(SearchShadowObservationModel.shadow_latency_ms),
                func.sum(func.case((SearchShadowObservationModel.error_category.is_not(None), 1), else_=0)),
            ).where(*conditions)
        ).one()
        return {"observations": int(count or 0), "average_top_k_overlap": float(overlap or 0), "average_shadow_latency_ms": float(latency or 0), "shadow_errors": int(errors or 0)}


class SearchShadowComparator:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        global_enabled: Callable[[], bool],
        max_timeout_ms: int,
    ):
        self.session_factory = session_factory
        self.global_enabled = global_enabled
        self.max_timeout_ms = max_timeout_ms
        self._tasks: set[asyncio.Task] = set()

    async def execute(
        self, *, tenant_id: str, query: str, primary: SearchCall,
        shadow: SearchCall, metadata_profile: str | None = None,
    ) -> Mapping[str, Any]:
        with self.session_factory() as session:
            policy = SearchShadowRepository(session).effective_policy(
                tenant_id, global_enabled=self.global_enabled(), max_timeout_ms=self.max_timeout_ms
            )
        started = time.perf_counter()
        primary_result = await primary()
        primary_ms = int((time.perf_counter() - started) * 1000)
        if policy.enabled and self._sampled(tenant_id, query, policy.sample_percentage):
            task = asyncio.create_task(self._observe(
                tenant_id=tenant_id, query=query, policy=policy,
                primary_result=primary_result, primary_ms=primary_ms,
                shadow=shadow, metadata_profile=metadata_profile,
            ))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return primary_result

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    @staticmethod
    def _sampled(tenant_id: str, query: str, percentage: int) -> bool:
        bucket = int(hashlib.sha256(f"{tenant_id}\0{query}".encode()).hexdigest()[:8], 16) % 10_000
        return bucket < percentage * 100

    async def _observe(self, *, tenant_id: str, query: str, policy: EffectiveShadowPolicy, primary_result: Mapping[str, Any], primary_ms: int, shadow: SearchCall, metadata_profile: str | None) -> None:
        started = time.perf_counter()
        error = None
        shadow_result: Mapping[str, Any] = {}
        try:
            shadow_result = await asyncio.wait_for(shadow(), timeout=policy.timeout_ms / 1000)
        except asyncio.TimeoutError:
            error = "timeout"
        except Exception as exc:
            error = type(exc).__name__[:64]
        shadow_ms = int((time.perf_counter() - started) * 1000)
        primary_ids = self._ids(primary_result)[:policy.top_k]
        shadow_ids = self._ids(shadow_result)[:policy.top_k]
        overlap = len(set(primary_ids) & set(shadow_ids)) / max(1, min(policy.top_k, len(set(primary_ids) | set(shadow_ids)))) if not error else None
        rank = self._rank_score(primary_ids, shadow_ids) if not error else None
        with self.session_factory() as session:
            SearchShadowRepository(session).save(SearchShadowObservationModel(
                tenant_id=tenant_id,
                query_hash=hashlib.sha256(query.encode()).hexdigest(),
                raw_query=query if policy.persist_raw_query and not _SENSITIVE.search(query) else None,
                query_type=self._query_type(query),
                query_features_json={"terms": len(query.split()), "quoted": '"' in query, "qualified": ":" in query, "or": " OR " in query.upper()},
                metadata_profile=metadata_profile,
                primary_version=policy.primary_version,
                shadow_version=policy.shadow_version,
                primary_latency_ms=primary_ms,
                shadow_latency_ms=shadow_ms,
                primary_count=self._count(primary_result),
                shadow_count=None if error else self._count(shadow_result),
                top_k_overlap=overlap,
                rank_correlation=rank,
                top_result_agrees=(primary_ids[:1] == shadow_ids[:1]) if not error else None,
                zero_result_disagrees=((not primary_ids) != (not shadow_ids)) if not error else None,
                error_category=error,
            ))

    @staticmethod
    def _ids(result: Mapping[str, Any]) -> list[str]:
        items = result.get("items") or result.get("hits") or []
        if isinstance(items, Mapping):
            items = items.get("hits") or []
        output = []
        for item in items if isinstance(items, Sequence) else ():
            if isinstance(item, Mapping):
                output.append(str(item.get("internal_asset_id") or item.get("asset_id") or item.get("_id") or item.get("id") or ""))
        return [item for item in output if item]

    @staticmethod
    def _count(result: Mapping[str, Any]) -> int:
        value = result.get("total")
        if isinstance(value, Mapping):
            value = value.get("value")
        return int(value if isinstance(value, (int, float)) else len(SearchShadowComparator._ids(result)))

    @staticmethod
    def _rank_score(primary: Sequence[str], shadow: Sequence[str]) -> float:
        shared = set(primary) & set(shadow)
        if not shared:
            return 0.0
        distance = sum(abs(primary.index(item) - shadow.index(item)) for item in shared)
        return max(0.0, 1.0 - distance / max(1, len(shared) * max(len(primary), len(shadow))))

    @staticmethod
    def _query_type(query: str) -> str:
        if " OR " in query.upper():
            return "or"
        if "," in query:
            return "strict_and"
        if '"' in query:
            return "phrase"
        if ":" in query:
            return "qualified"
        return "soft_and" if len(query.split()) > 1 else "term"
