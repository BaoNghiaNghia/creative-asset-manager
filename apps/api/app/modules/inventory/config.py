from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class InventoryWorkerConfig:
    automation_enabled: bool
    worker_enabled: bool
    drive_poller_enabled: bool
    worker_id: str
    concurrency: int
    lease_seconds: int
    heartbeat_seconds: float
    idle_poll_seconds: float
    drain_timeout_seconds: float
    health_host: str
    health_port: int
    tenant_allowlist: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> "InventoryWorkerConfig":
        return cls(
            automation_enabled=settings.INVENTORY_AUTOMATION_ENABLED,
            worker_enabled=settings.INVENTORY_WORKER_ENABLED,
            drive_poller_enabled=settings.INVENTORY_DRIVE_POLLER_ENABLED,
            worker_id=settings.INVENTORY_WORKER_ID or f"inventory-worker-{uuid4()}",
            concurrency=settings.INVENTORY_WORKER_CONCURRENCY,
            lease_seconds=settings.INVENTORY_WORKER_LEASE_SECONDS,
            heartbeat_seconds=settings.INVENTORY_WORKER_HEARTBEAT_SECONDS,
            idle_poll_seconds=settings.INVENTORY_WORKER_IDLE_POLL_SECONDS,
            drain_timeout_seconds=settings.INVENTORY_WORKER_DRAIN_TIMEOUT_SECONDS,
            health_host=settings.INVENTORY_WORKER_HEALTH_HOST,
            health_port=settings.INVENTORY_WORKER_HEALTH_PORT,
            tenant_allowlist=settings.inventory_tenant_allowlist,
        )

    @property
    def enabled(self) -> bool:
        return self.automation_enabled and self.worker_enabled
