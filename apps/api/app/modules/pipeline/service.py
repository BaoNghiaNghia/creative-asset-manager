from __future__ import annotations

from typing import Any, Mapping

from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.state import PipelineState
from app.modules.processing.repository import ProcessingRepository


PROVIDER_FOR_JOB = {
    "asset_store": ("google_drive", "storage"),
    "asset_analyze": ("gemini", "ai"),
    "asset_index": ("elasticsearch", "search"),
    "metadata_sidecar_export": ("google_drive", "storage"),
}

STATE_FOR_JOB = {
    "source_asset_download": PipelineState.DOWNLOAD_PENDING,
    "asset_store": PipelineState.STORAGE_PENDING,
    "asset_analyze": PipelineState.ANALYSIS_PENDING,
    "search_projection_build": PipelineState.PROJECTION_PENDING,
    "asset_index": PipelineState.SEARCH_PENDING,
    "metadata_sidecar_export": PipelineState.SIDECAR_PENDING,
}


class AssetPipelineService:
    """Atomically persists a transition and its next idempotent job."""

    def __init__(self, pipelines: AssetPipelineRepository, jobs: ProcessingRepository):
        if pipelines.session is not jobs.session:
            raise ValueError("pipeline and job repositories must share one session")
        self.pipelines = pipelines
        self.jobs = jobs

    def discover_and_enqueue(self, *, tenant_id: str, origin_type: str, origin_id: str,
                             source_asset_id: str | None = None,
                             correlation_id: str | None = None) -> AssetPipelineModel:
        pipeline = self.pipelines.get_or_create(
            tenant_id=tenant_id, origin_type=origin_type, origin_id=origin_id,
            source_asset_id=source_asset_id, correlation_id=correlation_id,
        )
        if pipeline.state == PipelineState.DISCOVERED.value:
            self.enqueue(pipeline, "source_asset_download", entity_type=origin_type, entity_id=origin_id)
        return pipeline

    def enqueue(self, pipeline: AssetPipelineModel, job_type: str, *,
                entity_type: str = "asset_pipeline", entity_id: str | None = None,
                payload: Mapping[str, Any] | None = None,
                transition: bool = True, provider_key: str | None = None,
                provider_scope: str | None = None) -> None:
        target = STATE_FOR_JOB[job_type]
        if transition and pipeline.state != target.value:
            self.pipelines.transition(pipeline, target)
        inferred_provider = PROVIDER_FOR_JOB.get(job_type)
        if inferred_provider:
            provider_key = provider_key or inferred_provider[0]
            provider_scope = provider_scope or inferred_provider[1]
        elif job_type == "source_asset_download":
            provider_key = provider_key or (pipeline.status_data_json or {}).get("source_provider")
            if provider_key is None and pipeline.origin_type == "ingestion_item":
                provider_key = "external_api"
            provider_scope = provider_scope or "source"
        self.jobs.create_job(
            tenant_id=pipeline.tenant_id, job_type=job_type,
            entity_type=entity_type, entity_id=entity_id or pipeline.id,
            idempotency_key=f"pipeline:{pipeline.id}:{job_type}:{self._identity(pipeline, job_type)}",
            payload={"pipeline_id": pipeline.id, "correlation_id": pipeline.correlation_id, **dict(payload or {})},
            provider_key=provider_key, provider_scope=provider_scope,
        )

    @staticmethod
    def _identity(pipeline: AssetPipelineModel, job_type: str) -> str:
        if job_type == "source_asset_download":
            return pipeline.source_asset_id or pipeline.origin_id
        if job_type == "asset_store":
            return pipeline.content_hash or "unresolved"
        if job_type == "asset_analyze":
            return pipeline.analysis_id or pipeline.asset_id or "unresolved"
        if job_type in {"search_projection_build", "asset_index"}:
            return f"{pipeline.analysis_id}:{pipeline.projection_version}:{pipeline.projection_checksum}"
        return pipeline.analysis_id or pipeline.asset_id or "unresolved"
