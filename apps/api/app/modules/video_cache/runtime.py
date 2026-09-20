"""Persisted master runtime gate for original-video CDN delivery.

The database row records operator rollout intent. Effective delivery additionally
requires cache, signed-delivery, rollout-scope and Phase 4E guard prerequisites.
Disabling the gate remains the application-level emergency rollback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.video_cache.model import (
    VIDEO_CDN_DELIVERY_SETTING_KEY,
    VideoDeliveryRuntimeSettingModel,
    utcnow,
)


class VideoDeliveryRuntimeUnavailable(RuntimeError):
    """The persisted runtime setting cannot be resolved safely."""


class VideoDeliveryPrerequisiteError(ValueError):
    """Enabling was requested before the delivery prerequisites were ready."""


@dataclass(frozen=True)
class VideoDeliveryRuntimeStatus:
    setting: str
    runtime_enabled: bool
    effective_enabled: bool
    can_enable: bool
    prerequisites: dict[str, bool]
    blockers: tuple[str, ...]
    rollout_mode: str
    canary_tenant_count: int
    updated_at: datetime | None

    def as_dict(self) -> dict:
        return {
            "setting": self.setting,
            "runtime_enabled": self.runtime_enabled,
            "effective_enabled": self.effective_enabled,
            "can_enable": self.can_enable,
            "prerequisites": dict(self.prerequisites),
            "blockers": list(self.blockers),
            "rollout_mode": self.rollout_mode,
            "canary_tenant_count": self.canary_tenant_count,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class VideoDeliveryRuntimeService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def _row(self) -> VideoDeliveryRuntimeSettingModel:
        row = self.session.get(
            VideoDeliveryRuntimeSettingModel,
            VIDEO_CDN_DELIVERY_SETTING_KEY,
        )
        if row is None:
            raise VideoDeliveryRuntimeUnavailable(
                "Video delivery runtime configuration is unavailable"
            )
        return row

    def _status(self, row: VideoDeliveryRuntimeSettingModel) -> VideoDeliveryRuntimeStatus:
        cache_enabled = bool(self.settings.R2_VIDEO_CACHE_ENABLED)
        delivery_configured = bool(self.settings.video_delivery_configured)
        rollout_mode = self.settings.video_delivery_rollout_mode
        rollout_configured = rollout_mode != "disabled"
        guard_enabled = bool(self.settings.VIDEO_CDN_DELIVERY_GUARD_ENABLED)
        can_enable = (
            cache_enabled
            and delivery_configured
            and rollout_configured
            and guard_enabled
        )
        runtime_enabled = bool(row.enabled)
        canary_tenant_count = len(self.settings.video_delivery_canary_tenant_ids)
        blockers: list[str] = []
        if not runtime_enabled:
            blockers.append("runtime_toggle_disabled")
        if not cache_enabled:
            blockers.append("r2_video_cache_disabled")
        if not delivery_configured:
            blockers.append("video_delivery_config_missing")
        if rollout_mode == "disabled":
            blockers.append("video_delivery_rollout_scope_empty")
        if not guard_enabled:
            blockers.append("video_delivery_guard_disabled")
        return VideoDeliveryRuntimeStatus(
            setting=VIDEO_CDN_DELIVERY_SETTING_KEY,
            runtime_enabled=runtime_enabled,
            effective_enabled=runtime_enabled and can_enable,
            can_enable=can_enable,
            prerequisites={
                "r2_video_cache_enabled": cache_enabled,
                "delivery_configured": delivery_configured,
                "rollout_scope_configured": rollout_configured,
                "delivery_guard_enabled": guard_enabled,
            },
            blockers=tuple(blockers),
            rollout_mode=rollout_mode,
            canary_tenant_count=canary_tenant_count,
            updated_at=row.updated_at,
        )

    def get_status(self) -> dict:
        return self._status(self._row()).as_dict()

    def set_enabled(
        self,
        enabled: bool,
        *,
        actor_id: str,
        reason: str,
    ) -> dict:
        normalized_reason = reason.strip()
        if not 3 <= len(normalized_reason) <= 500:
            raise ValueError("A change reason between 3 and 500 characters is required")

        row = self._row()
        current = self._status(row)
        if enabled and not current.can_enable:
            raise VideoDeliveryPrerequisiteError(
                "Video delivery prerequisites are unavailable"
            )

        previous_enabled = bool(row.enabled)
        row.enabled = bool(enabled)
        row.updated_by = actor_id
        row.update_reason = normalized_reason
        row.updated_at = utcnow()
        self.session.add(
            AuthAuditEventModel(
                tenant_id=None,
                actor_id=actor_id,
                action="video_cdn_delivery_runtime_updated",
                detail_json={
                    "setting": VIDEO_CDN_DELIVERY_SETTING_KEY,
                    "previous_enabled": previous_enabled,
                    "enabled": bool(enabled),
                    "reason": normalized_reason,
                },
            )
        )
        self.session.flush()
        return self._status(row).as_dict()
