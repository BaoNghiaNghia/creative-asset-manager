from __future__ import annotations

from collections.abc import Callable

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


def build_inventory_handler_registry() -> InventoryHandlerRegistry:
    """Phase 1 intentionally exposes no production business handlers."""
    return InventoryHandlerRegistry()
