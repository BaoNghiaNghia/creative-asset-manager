from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.core.config import Settings
from app.core.database import SessionLocal
from app.modules.inventory.drive.downloader import InventoryFileDownloader
from app.modules.inventory.drive.poller import INVENTORY_FILE_DOWNLOAD_JOB
from app.modules.inventory.drive.storage import InventorySourceStorage
from app.modules.inventory.jobs.model import InventoryJobModel

InventoryJobHandler = Callable[[InventoryJobModel], None]


class InventoryHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, InventoryJobHandler] = {}

    def register(self, job_type: str, handler: InventoryJobHandler) -> None:
        if not job_type or not job_type.startswith("inventory_"):
            raise ValueError("Inventory job types must use the inventory_ namespace")
        if job_type in self._handlers:
            raise ValueError(f"Inventory handler already registered for {job_type}")
        self._handlers[job_type] = handler

    def resolve(self, job_type: str) -> InventoryJobHandler | None:
        return self._handlers.get(job_type)

    @property
    def job_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)


def build_inventory_handler_registry(
    settings: Settings | None = None,
    *,
    downloader: InventoryFileDownloader | None = None,
) -> InventoryHandlerRegistry:
    """Register only handlers delivered by completed Inventory phases."""
    runtime_settings = settings or Settings()
    runtime_downloader = downloader or InventoryFileDownloader(
        SessionLocal,
        storage=InventorySourceStorage(
            runtime_settings.INVENTORY_SOURCE_STORAGE_ROOT
        ),
        max_bytes=runtime_settings.INVENTORY_DOWNLOAD_MAX_BYTES,
    )
    registry = InventoryHandlerRegistry()

    def download(job: InventoryJobModel) -> None:
        asyncio.run(runtime_downloader.execute(job))

    registry.register(INVENTORY_FILE_DOWNLOAD_JOB, download)
    return registry
