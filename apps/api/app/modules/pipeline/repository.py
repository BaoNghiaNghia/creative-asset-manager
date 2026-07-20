from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.state import PipelineState, validate_transition


class AssetPipelineRepository:
    """Durable pipeline persistence. Methods flush and never commit."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, tenant_id: str, pipeline_id: str, *, for_update: bool = False) -> AssetPipelineModel | None:
        statement = select(AssetPipelineModel).where(
            AssetPipelineModel.tenant_id == tenant_id,
            AssetPipelineModel.id == pipeline_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_by_origin(self, tenant_id: str, origin_type: str, origin_id: str) -> AssetPipelineModel | None:
        return self.session.scalar(select(AssetPipelineModel).where(
            AssetPipelineModel.tenant_id == tenant_id,
            AssetPipelineModel.origin_type == origin_type,
            AssetPipelineModel.origin_id == origin_id,
        ))

    def get_or_create(self, *, tenant_id: str, origin_type: str, origin_id: str,
                      source_asset_id: str | None = None,
                      correlation_id: str | None = None) -> AssetPipelineModel:
        existing = self.get_by_origin(tenant_id, origin_type, origin_id)
        if existing is not None:
            if source_asset_id and not existing.source_asset_id:
                existing.source_asset_id = source_asset_id
                self.session.flush()
            return existing
        try:
            with self.session.begin_nested():
                pipeline = AssetPipelineModel(
                    tenant_id=tenant_id,
                    correlation_id=correlation_id or f"asset:{origin_type}:{origin_id}",
                    origin_type=origin_type,
                    origin_id=origin_id,
                    source_asset_id=source_asset_id,
                )
                self.session.add(pipeline)
                self.session.flush()
            return pipeline
        except IntegrityError:
            existing = self.get_by_origin(tenant_id, origin_type, origin_id)
            if existing is None:
                raise
            return existing

    def transition(self, pipeline: AssetPipelineModel, state: PipelineState | str, *,
                   status_data: Mapping[str, Any] | None = None,
                   error_code: str | None = None,
                   error_message: str | None = None,
                   retryable: bool | None = None) -> AssetPipelineModel:
        target = PipelineState(state)
        validate_transition(pipeline.state, target.value)
        pipeline.state = target.value
        pipeline.last_error_code = error_code[:100] if error_code else None
        pipeline.last_error_message = error_message
        pipeline.failure_retryable = retryable
        if status_data:
            pipeline.status_data_json = {**(pipeline.status_data_json or {}), **dict(status_data)}
        now = datetime.now(timezone.utc)
        pipeline.updated_at = now
        pipeline.completed_at = now if target == PipelineState.COMPLETED else None
        self.session.flush()
        return pipeline

    def record_failure(self, pipeline: AssetPipelineModel, stage: str, *,
                       error_code: str, error_message: str,
                       retryable: bool) -> AssetPipelineModel:
        return self.transition(
            pipeline, PipelineState(f"{stage}_failed"), error_code=error_code,
            error_message=error_message, retryable=retryable,
        )
