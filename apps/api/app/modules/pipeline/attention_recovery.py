from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.service import AssetPipelineService
from app.modules.pipeline.state import PipelineState
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository


STORAGE_FALLBACK_CODES = frozenset({
    "managed_storage_forbidden",
    "managed_storage_unauthorized",
})
ANALYSIS_RECOVERY_CODES = frozenset({
    "managed_asset_storage_failed",
    "managed_asset_storage_missing",
    "gemini_invalid_json",
    "gemini_invalid_document",
    "gemini_empty_response",
})


@dataclass(slots=True)
class PipelineAttentionRecoveryResult:
    storage_pipelines: int = 0
    analysis_jobs: int = 0
    projection_jobs: int = 0
    skipped_missing_identity: int = 0
    skipped_no_profile: int = 0

    def document(self) -> dict[str, int]:
        return asdict(self)


class PipelineAttentionRecovery:
    """Repair retry-safe image pipeline failures without duplicating jobs."""

    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.pipelines = AssetPipelineRepository(session)
        self.jobs = ProcessingRepository(session)
        self.coordinator = AssetPipelineService(self.pipelines, self.jobs)

    def preview(self, tenant_id: str) -> PipelineAttentionRecoveryResult:
        result = PipelineAttentionRecoveryResult()
        result.storage_pipelines = len(list(self.session.scalars(
            select(AssetPipelineModel.id).where(
                AssetPipelineModel.tenant_id == tenant_id,
                AssetPipelineModel.state == PipelineState.STORAGE_FAILED.value,
                AssetPipelineModel.last_error_code.in_(STORAGE_FALLBACK_CODES),
            )
        )))
        result.analysis_jobs = len(list(self.session.scalars(
            select(ProcessingJobModel.id).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "asset_analyze",
                ProcessingJobModel.status == "failed",
                ProcessingJobModel.last_error_code.in_(ANALYSIS_RECOVERY_CODES),
            )
        )))
        result.projection_jobs = len(list(self.session.scalars(
            select(ProcessingJobModel.id).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "search_projection_build",
                ProcessingJobModel.status == "failed",
                ProcessingJobModel.last_error_code == "ValueError",
            )
        )))
        return result

    def apply(self, tenant_id: str, *, limit: int = 5000) -> PipelineAttentionRecoveryResult:
        if not self.settings.AI_ANALYSIS_SOURCE_FALLBACK_ENABLED:
            raise RuntimeError("source_analysis_fallback_disabled")
        result = PipelineAttentionRecoveryResult()

        storage_rows = list(self.session.scalars(
            select(AssetPipelineModel)
            .where(
                AssetPipelineModel.tenant_id == tenant_id,
                AssetPipelineModel.state == PipelineState.STORAGE_FAILED.value,
                AssetPipelineModel.last_error_code.in_(STORAGE_FALLBACK_CODES),
            )
            .order_by(AssetPipelineModel.updated_at, AssetPipelineModel.id)
            .limit(limit)
            .with_for_update()
        ))
        for pipeline in storage_rows:
            if not pipeline.asset_id or not pipeline.source_asset_id:
                result.skipped_missing_identity += 1
                continue
            if not self._continue_from_source(pipeline):
                result.skipped_no_profile += 1
                continue
            result.storage_pipelines += 1

        remaining = max(0, limit - result.storage_pipelines)
        if remaining:
            failed_analysis_jobs = list(self.session.scalars(
                select(ProcessingJobModel)
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.status == "failed",
                    ProcessingJobModel.last_error_code.in_(ANALYSIS_RECOVERY_CODES),
                )
                .order_by(ProcessingJobModel.updated_at, ProcessingJobModel.id)
                .limit(remaining)
                .with_for_update()
            ))
            for job in failed_analysis_jobs:
                pipeline = self.pipelines.get(tenant_id, job.entity_id, for_update=True)
                if pipeline is None or not pipeline.asset_id or not pipeline.source_asset_id:
                    result.skipped_missing_identity += 1
                    continue
                if not self._prepare_analysis_pipeline(pipeline, job):
                    result.skipped_no_profile += 1
                    continue
                result.analysis_jobs += 1

        self._repair_projection_jobs(tenant_id, result, limit=max(0, limit - result.analysis_jobs))
        self.session.flush()
        return result

    def _latest_completed_analysis(self, pipeline: AssetPipelineModel) -> AssetAiAnalysisModel | None:
        if not pipeline.asset_id:
            return None
        return self.session.scalar(
            select(AssetAiAnalysisModel)
            .where(
                AssetAiAnalysisModel.tenant_id == pipeline.tenant_id,
                AssetAiAnalysisModel.asset_id == pipeline.asset_id,
                AssetAiAnalysisModel.status == "completed",
            )
            .order_by(AssetAiAnalysisModel.completed_at.desc(), AssetAiAnalysisModel.id.desc())
            .limit(1)
        )

    def _active_profile(self, tenant_id: str) -> MetadataProfileModel | None:
        return self.session.scalar(
            select(MetadataProfileModel)
            .where(
                MetadataProfileModel.tenant_id == tenant_id,
                MetadataProfileModel.active.is_(True),
            )
            .order_by(MetadataProfileModel.created_at.desc(), MetadataProfileModel.id.desc())
            .limit(1)
        )

    def _analysis_for_pipeline(self, pipeline: AssetPipelineModel) -> AssetAiAnalysisModel | None:
        if pipeline.analysis_id:
            analysis = self.session.get(AssetAiAnalysisModel, pipeline.analysis_id)
            if analysis is not None and analysis.tenant_id == pipeline.tenant_id:
                return analysis
        if not pipeline.asset_id:
            return None
        analysis = self.session.scalar(
            select(AssetAiAnalysisModel)
            .where(
                AssetAiAnalysisModel.tenant_id == pipeline.tenant_id,
                AssetAiAnalysisModel.asset_id == pipeline.asset_id,
            )
            .order_by(AssetAiAnalysisModel.created_at.desc(), AssetAiAnalysisModel.id.desc())
            .limit(1)
        )
        if analysis is not None:
            pipeline.analysis_id = analysis.id
            return analysis
        profile = self._active_profile(pipeline.tenant_id)
        if profile is None:
            return None
        analysis = AiMetadataRepository(self.session).create_analysis(
            tenant_id=pipeline.tenant_id,
            asset_id=pipeline.asset_id,
            metadata_profile_id=profile.id,
            prompt_version="auto-v1",
            pipeline_version="asset-pipeline-v1",
            ai_provider="gemini",
        )
        pipeline.analysis_id = analysis.id
        return analysis

    def _continue_from_source(self, pipeline: AssetPipelineModel) -> bool:
        completed = self._latest_completed_analysis(pipeline)
        if completed is not None:
            pipeline.analysis_id = completed.id
            self.coordinator.enqueue(pipeline, "search_projection_build")
            self._reset_pipeline_job(pipeline, "search_projection_build")
            return True
        analysis = self._analysis_for_pipeline(pipeline)
        if analysis is None:
            return False
        if pipeline.state == PipelineState.STORAGE_FAILED.value:
            self.pipelines.transition(pipeline, PipelineState.ANALYSIS_PENDING)
        self.coordinator.enqueue(
            pipeline,
            "asset_analyze",
            payload={
                "analysis_id": analysis.id,
                "analysis_content_source": "source_asset",
            },
            transition=False,
        )
        job = self._pipeline_job(pipeline, "asset_analyze")
        if job is not None:
            self._reset_analysis_job(job, analysis)
        return True

    def _prepare_analysis_pipeline(
        self, pipeline: AssetPipelineModel, job: ProcessingJobModel
    ) -> bool:
        analysis = self._analysis_for_pipeline(pipeline)
        if analysis is None:
            return False
        if pipeline.state in {
            PipelineState.STORAGE_FAILED.value,
            PipelineState.ANALYSIS_FAILED.value,
            PipelineState.STORED.value,
            PipelineState.DOWNLOADED.value,
            PipelineState.DUPLICATE_DETECTED.value,
        }:
            self.pipelines.transition(pipeline, PipelineState.ANALYSIS_PENDING)
        elif pipeline.state != PipelineState.ANALYSIS_PENDING.value:
            return False
        payload = dict(job.payload_json or {})
        payload.update({
            "pipeline_id": pipeline.id,
            "analysis_id": analysis.id,
            "analysis_content_source": "source_asset",
        })
        job.payload_json = payload
        self._reset_analysis_job(job, analysis)
        return True

    def _pipeline_job(
        self, pipeline: AssetPipelineModel, job_type: str
    ) -> ProcessingJobModel | None:
        identity = AssetPipelineService._identity(pipeline, job_type)
        return self.jobs.get_job_by_key(
            pipeline.tenant_id,
            f"pipeline:{pipeline.id}:{job_type}:{identity}",
        )

    @staticmethod
    def _reset_job(job: ProcessingJobModel) -> None:
        now = datetime.now(timezone.utc)
        job.status = "retry"
        job.next_attempt_at = now
        job.completed_at = None
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.cancellation_requested = False
        job.cancel_requested_at = None
        job.cancel_requested_by = None
        job.cancellation_reason = None
        job.max_attempts = max(job.max_attempts, job.attempt_count + 3)
        job.last_error_code = "operator_recovery_requested"
        job.last_error_message = "Pipeline recovery requested after source fallback repair."
        job.updated_at = now

    def _reset_analysis_job(
        self, job: ProcessingJobModel, analysis: AssetAiAnalysisModel
    ) -> None:
        self._reset_job(job)
        payload = dict(job.payload_json or {})
        payload["analysis_content_source"] = "source_asset"
        payload["analysis_id"] = analysis.id
        job.payload_json = payload
        if analysis.status != "completed":
            analysis.status = "pending"
            analysis.processing_stage = "retry"
            analysis.claimed_by = None
            analysis.lease_expires_at = None
            analysis.last_error_code = None
            analysis.last_error_message = None
            analysis.updated_at = datetime.now(timezone.utc)

    def _reset_pipeline_job(self, pipeline: AssetPipelineModel, job_type: str) -> None:
        job = self._pipeline_job(pipeline, job_type)
        if job is not None and job.status == "failed":
            self._reset_job(job)

    def _repair_projection_jobs(
        self,
        tenant_id: str,
        result: PipelineAttentionRecoveryResult,
        *,
        limit: int,
    ) -> None:
        if limit <= 0:
            return
        rows = list(self.session.scalars(
            select(ProcessingJobModel)
            .where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "search_projection_build",
                ProcessingJobModel.status == "failed",
                ProcessingJobModel.last_error_code == "ValueError",
            )
            .order_by(ProcessingJobModel.updated_at, ProcessingJobModel.id)
            .limit(limit)
            .with_for_update()
        ))
        for job in rows:
            pipeline = self.pipelines.get(tenant_id, job.entity_id, for_update=True)
            if pipeline is None:
                continue
            completed = self._latest_completed_analysis(pipeline)
            if completed is None:
                continue
            pipeline.analysis_id = completed.id
            if pipeline.state == PipelineState.PROJECTION_FAILED.value:
                self.pipelines.transition(pipeline, PipelineState.PROJECTION_PENDING)
            if pipeline.state != PipelineState.PROJECTION_PENDING.value:
                continue
            self._reset_job(job)
            result.projection_jobs += 1
