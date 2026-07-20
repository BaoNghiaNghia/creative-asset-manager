from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from app.core.config import Settings
from app.modules.processing_policy.repository import ProcessingPolicyRepository, policy_document


STAGE_GLOBALS = {
    "pipeline_enabled": ("UNIFIED_ASSET_INGESTION_ENABLED", "PROCESSING_JOBS_ENABLED"),
    "source_sync_enabled": ("UNIFIED_ASSET_INGESTION_ENABLED", "INCREMENTAL_SOURCE_SYNC_ENABLED", "PROCESSING_JOBS_ENABLED"),
    "download_enabled": ("UNIFIED_ASSET_INGESTION_ENABLED", "CONTENT_DEDUP_ENABLED", "PROCESSING_JOBS_ENABLED"),
    "managed_storage_enabled": ("UNIFIED_ASSET_INGESTION_ENABLED", "MANAGED_ASSET_STORAGE_ENABLED", "PROCESSING_JOBS_ENABLED"),
    "ai_analysis_enabled": ("UNIFIED_ASSET_INGESTION_ENABLED", "DYNAMIC_AI_METADATA_ENABLED", "AI_SINGLE_ANALYSIS_ENABLED", "PROCESSING_JOBS_ENABLED"),
    "search_v2_enabled": ("UNIFIED_ASSET_INGESTION_ENABLED", "SEARCH_PROJECTION_ENABLED", "ELASTICSEARCH_V2_ENABLED", "PROCESSING_JOBS_ENABLED"),
    "sidecar_enabled": ("UNIFIED_ASSET_INGESTION_ENABLED", "DRIVE_METADATA_SIDECAR_ENABLED", "PROCESSING_JOBS_ENABLED"),
}


class TenantPolicyCache:
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._values: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get(self, tenant_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._values.get(tenant_id)
            if item is None or item[0] <= time.monotonic():
                self._values.pop(tenant_id, None)
                return None
            return dict(item[1])

    def set(self, tenant_id: str, value: Mapping[str, Any]) -> None:
        with self._lock:
            self._values[tenant_id] = (
                time.monotonic() + self.ttl_seconds,
                dict(value),
            )

    def invalidate(self, tenant_id: str) -> None:
        with self._lock:
            self._values.pop(tenant_id, None)


@dataclass(frozen=True, slots=True)
class EffectiveTenantPolicy:
    tenant_id: str
    configured: Mapping[str, Any]
    effective: Mapping[str, bool]
    global_upper_bounds: Mapping[str, bool]


class ProcessingPolicyService:
    def __init__(self, repository: ProcessingPolicyRepository, settings: Settings,
                 cache: TenantPolicyCache | None = None):
        self.repository = repository
        self.settings = settings
        self.cache = cache

    def effective(self, tenant_id: str) -> EffectiveTenantPolicy:
        configured = self.cache.get(tenant_id) if self.cache else None
        if configured is None:
            policy = self.repository.get_or_create_tenant(tenant_id)
            configured = policy_document(policy)
            if self.cache:
                self.cache.set(tenant_id, configured)
        bounds = {
            stage: all(bool(getattr(self.settings, flag)) for flag in flags)
            for stage, flags in STAGE_GLOBALS.items()
        }
        paused = bool(configured.get("processing_paused"))
        pipeline_effective = bool(configured.get("pipeline_enabled")) and bounds["pipeline_enabled"] and not paused
        effective = {
            stage: (
                bool(configured.get(stage)) and bounds[stage] and not paused
                and (stage == "pipeline_enabled" or pipeline_effective)
            )
            for stage in STAGE_GLOBALS
        }
        return EffectiveTenantPolicy(tenant_id, configured, effective, bounds)

    def update(self, tenant_id: str, changes: Mapping[str, Any], *, actor_id: str,
               reason: str | None = None):
        before = policy_document(self.repository.get_or_create_tenant(tenant_id))
        policy = self.repository.update_tenant(tenant_id, changes)
        after = policy_document(policy)
        self.repository.audit(
            actor_id=actor_id, tenant_id=tenant_id, action="tenant_policy_updated",
            old_policy=before, new_policy=after, reason=reason,
        )
        if self.cache:
            self.cache.invalidate(tenant_id)
        return policy

    def pause(self, tenant_id: str, *, actor_id: str, reason: str):
        before = policy_document(self.repository.get_or_create_tenant(tenant_id))
        policy = self.repository.pause_tenant(tenant_id, actor_id=actor_id, reason=reason)
        self.repository.audit(
            actor_id=actor_id, tenant_id=tenant_id, action="tenant_paused",
            old_policy=before, new_policy=policy_document(policy), reason=reason,
        )
        if self.cache:
            self.cache.invalidate(tenant_id)
        return policy

    def resume(self, tenant_id: str, *, actor_id: str, reason: str | None = None):
        before = policy_document(self.repository.get_or_create_tenant(tenant_id))
        policy = self.repository.resume_tenant(tenant_id)
        self.repository.audit(
            actor_id=actor_id, tenant_id=tenant_id, action="tenant_resumed",
            old_policy=before, new_policy=policy_document(policy), reason=reason,
        )
        if self.cache:
            self.cache.invalidate(tenant_id)
        return policy

    def update_provider(self, tenant_id: str, provider_key: str, provider_scope: str,
                        changes: Mapping[str, Any], *, actor_id: str, reason: str | None = None):
        current = self.repository.get_or_create_provider(tenant_id, provider_key, provider_scope)
        before = policy_document(current)
        current = self.repository.update_provider(tenant_id, provider_key, provider_scope, changes)
        self.repository.audit(
            actor_id=actor_id, tenant_id=tenant_id, action="provider_policy_updated",
            old_policy=before, new_policy=policy_document(current), reason=reason,
            provider_key=provider_key, provider_scope=provider_scope,
        )
        return current

    def set_provider_pause(self, tenant_id: str, provider_key: str, provider_scope: str,
                           *, paused: bool, actor_id: str, reason: str | None = None):
        current = self.repository.get_or_create_provider(tenant_id, provider_key, provider_scope)
        before = policy_document(current)
        if paused:
            current = self.repository.pause_provider(
                tenant_id, provider_key, provider_scope,
                actor_id=actor_id, reason=reason or "Operator pause",
            )
            action = "provider_paused"
        else:
            current = self.repository.resume_provider(tenant_id, provider_key, provider_scope)
            action = "provider_resumed"
        self.repository.audit(
            actor_id=actor_id, tenant_id=tenant_id, action=action,
            old_policy=before, new_policy=policy_document(current), reason=reason,
            provider_key=provider_key, provider_scope=provider_scope,
        )
        return current
