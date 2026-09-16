"""Deny-by-default Creative Pipeline canary/rollout policy."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


def _ids(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class CreativePipelineRolloutPolicy:
    """Exact tenant + Source Group gates for execution, never discovery visibility."""

    enabled: bool
    tenant_ids: frozenset[str]
    external_source_ids: frozenset[str]
    source_group_folder_ids: frozenset[str]
    listing_folder_ids: frozenset[str]
    max_active_runs: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "CreativePipelineRolloutPolicy":
        return cls(
            enabled=bool(settings.CREATIVE_PIPELINE_ROLLOUT_ENABLED),
            tenant_ids=_ids(settings.CREATIVE_PIPELINE_ROLLOUT_TENANT_IDS),
            external_source_ids=_ids(settings.CREATIVE_PIPELINE_ROLLOUT_EXTERNAL_SOURCE_IDS),
            source_group_folder_ids=_ids(settings.CREATIVE_PIPELINE_ROLLOUT_SOURCE_GROUP_FOLDER_IDS),
            listing_folder_ids=_ids(settings.CREATIVE_PIPELINE_ROLLOUT_LISTING_FOLDER_IDS),
            max_active_runs=max(1, int(settings.CREATIVE_PIPELINE_ROLLOUT_MAX_ACTIVE_RUNS)),
        )

    def allows_source(self, *, tenant_id: str, external_source_id: str) -> bool:
        return (
            self.enabled
            and tenant_id in self.tenant_ids
            and external_source_id in self.external_source_ids
        )

    def allows_group(self, *, tenant_id: str, external_source_id: str, external_folder_id: str | None) -> bool:
        return bool(
            self.allows_source(tenant_id=tenant_id, external_source_id=external_source_id)
            and external_folder_id
            and external_folder_id in self.source_group_folder_ids
        )

    def allows_listing(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        source_group_folder_id: str | None,
        listing_folder_id: str | None,
    ) -> bool:
        if not self.allows_group(
            tenant_id=tenant_id,
            external_source_id=external_source_id,
            external_folder_id=source_group_folder_id,
        ):
            return False
        return not self.listing_folder_ids or bool(
            listing_folder_id and listing_folder_id in self.listing_folder_ids
        )

    def reason_for_listing(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        source_group_folder_id: str | None,
        listing_folder_id: str | None,
    ) -> str | None:
        if not self.enabled:
            return "creative_pipeline_rollout_disabled"
        if tenant_id not in self.tenant_ids:
            return "creative_pipeline_rollout_tenant_denied"
        if external_source_id not in self.external_source_ids:
            return "creative_pipeline_rollout_source_denied"
        if not source_group_folder_id or source_group_folder_id not in self.source_group_folder_ids:
            return "creative_pipeline_rollout_group_denied"
        if self.listing_folder_ids and (
            not listing_folder_id or listing_folder_id not in self.listing_folder_ids
        ):
            return "creative_pipeline_rollout_listing_denied"
        return None
