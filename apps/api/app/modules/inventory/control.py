from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.inventory.model import InventoryProcessingControlModel


class InventoryControlRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(self, tenant_id: str) -> InventoryProcessingControlModel:
        control = self.session.scalar(
            select(InventoryProcessingControlModel).where(
                InventoryProcessingControlModel.tenant_id == tenant_id
            )
        )
        if control is None:
            control = InventoryProcessingControlModel(tenant_id=tenant_id)
            self.session.add(control)
            self.session.flush()
        return control

    def configure(
        self,
        tenant_id: str,
        *,
        enabled: bool,
        paused: bool,
        max_active_jobs: int,
        max_ai_jobs: int,
    ) -> InventoryProcessingControlModel:
        if max_active_jobs <= 0:
            raise ValueError("max_active_jobs must be positive")
        if max_ai_jobs < 0 or max_ai_jobs > max_active_jobs:
            raise ValueError("max_ai_jobs must be between zero and max_active_jobs")
        control = self.get_or_create(tenant_id)
        control.enabled = enabled
        control.paused = paused
        control.max_active_jobs = max_active_jobs
        control.max_ai_jobs = max_ai_jobs
        self.session.flush()
        return control
