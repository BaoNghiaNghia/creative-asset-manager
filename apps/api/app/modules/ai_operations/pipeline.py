from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, literal, select
from sqlalchemy.orm import Session

from app.domain.processing.types import JobStatus
from app.modules.assets.model import SourceAssetModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.processing.model import (
    PROCESSING_JOB_QUEUED_STATUSES,
    PROCESSING_JOB_RUNNING_STATUSES,
    ProcessingJobModel,
)
from app.modules.source_sync.model import SourceSyncRunModel


PIPELINE_STAGES: tuple[tuple[str, str, str], ...] = (
    ("source_asset_download", "Download", "Download supported source images from Google Drive."),
    ("asset_store", "Store", "Save asset content and source linkage."),
    ("asset_analyze", "AI Analyze", "Generate metadata for imported assets."),
    ("search_projection_build", "Search Projection", "Build searchable metadata."),
    ("asset_index", "Elasticsearch Index", "Index the search document."),
)
SUPPORTED_IMAGE_MIME_TYPES = ("image/jpeg", "image/png", "image/webp")


class PipelineOperationsRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _waiting(status, next_attempt_at, now: datetime):
        return (
            (status == JobStatus.RETRY.value)
            | (
                status.in_(PROCESSING_JOB_QUEUED_STATUSES)
                & next_attempt_at.is_not(None)
                & (next_attempt_at > now)
            )
        )

    def _latest_jobs(self, tenant_id: str):
        ranked = select(
            ProcessingJobModel.id.label("job_id"),
            func.row_number().over(
                partition_by=(
                    ProcessingJobModel.tenant_id,
                    ProcessingJobModel.job_type,
                    ProcessingJobModel.entity_type,
                    ProcessingJobModel.entity_id,
                ),
                order_by=(
                    ProcessingJobModel.created_at.desc(),
                    ProcessingJobModel.updated_at.desc(),
                    ProcessingJobModel.id.desc(),
                ),
            ).label("position"),
        ).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type.in_([stage[0] for stage in PIPELINE_STAGES]),
        ).subquery()
        return select(ProcessingJobModel).join(
            ranked, ranked.c.job_id == ProcessingJobModel.id,
        ).where(ranked.c.position == 1).subquery()

    def _stage_counts(self, tenant_id: str, now: datetime) -> dict[str, dict[str, Any]]:
        latest = self._latest_jobs(tenant_id)
        waiting = self._waiting(latest.c.status, latest.c.next_attempt_at, now)
        eligible = latest.c.status.in_(PROCESSING_JOB_QUEUED_STATUSES) & ~waiting & (
            latest.c.next_attempt_at.is_(None) | (latest.c.next_attempt_at <= now)
        )
        rows = self.session.execute(select(
            latest.c.job_type,
            func.count(),
            func.coalesce(func.sum(case((eligible, 1), else_=0)), 0),
            func.coalesce(func.sum(case((waiting, 1), else_=0)), 0),
            func.coalesce(func.sum(case((latest.c.status.in_(PROCESSING_JOB_RUNNING_STATUSES), 1), else_=0)), 0),
            func.coalesce(func.sum(case((latest.c.status == JobStatus.COMPLETED.value, 1), else_=0)), 0),
            func.coalesce(func.sum(case((latest.c.status == JobStatus.FAILED.value, 1), else_=0)), 0),
            func.min(case((eligible, latest.c.created_at), else_=None)),
        ).group_by(latest.c.job_type)).all()
        counts = {
            stage: {
                "key": stage, "label": label, "subtitle": subtitle,
                "total": 0, "pending": 0, "eligible_now": 0, "waiting": 0,
                "processing": 0, "completed": 0, "failed": 0,
                "percentage": None, "oldest_pending_at": None,
            }
            for stage, label, subtitle in PIPELINE_STAGES
        }
        for stage, total, eligible_now, waiting_count, processing, completed, failed, oldest in rows:
            item = counts[stage]
            item.update(
                total=int(total or 0),
                pending=int(eligible_now or 0) + int(waiting_count or 0),
                eligible_now=int(eligible_now or 0),
                waiting=int(waiting_count or 0),
                processing=int(processing or 0),
                completed=int(completed or 0),
                failed=int(failed or 0),
                oldest_pending_at=oldest,
            )
            denominator = item["total"]
            item["percentage"] = round((item["completed"] / denominator) * 100, 1) if denominator else None
        return counts

    def _latest_source_sync(self, tenant_id: str) -> dict[str, Any] | None:
        run = self.session.scalar(select(SourceSyncRunModel).where(
            SourceSyncRunModel.tenant_id == tenant_id,
        ).order_by(
            SourceSyncRunModel.started_at.desc(),
            SourceSyncRunModel.updated_at.desc(),
            SourceSyncRunModel.id.desc(),
        ).limit(1))
        if run is None:
            return None
        finished = self._aware(run.completed_at or run.failed_at)
        started_at = self._aware(run.started_at)
        duration_ms = None
        if finished is not None and started_at is not None:
            duration_ms = max(0, int((finished - started_at).total_seconds() * 1000))
        error_code = (run.error_json or {}).get("code") if isinstance(run.error_json, dict) else None
        return {
            "mode": run.mode, "status": run.status, "pages_count": run.pages_count,
            "items_seen_count": run.items_seen_count, "jobs_created_count": run.jobs_created_count,
            "started_at": run.started_at, "completed_at": run.completed_at,
            "duration_ms": duration_ms, "error_code": error_code,
        }

    def _active_job(self, tenant_id: str, now: datetime) -> dict[str, Any] | None:
        job = self.session.scalar(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type.in_([stage[0] for stage in PIPELINE_STAGES]),
            ProcessingJobModel.status.in_(PROCESSING_JOB_RUNNING_STATUSES),
        ).order_by(ProcessingJobModel.claimed_at.asc(), ProcessingJobModel.created_at.asc()).limit(1))
        if job is None:
            return None
        filename = None
        if job.job_type == "source_asset_download":
            filename = self.session.scalar(select(SourceAssetModel.filename).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.id == job.entity_id,
            ))
        labels = {
            "source_asset_download": "Downloading from Google Drive",
            "asset_store": "Saving asset content and source linkage",
            "asset_analyze": "Analyzing metadata with " + (job.provider_key or "the configured provider").title(),
            "search_projection_build": "Building searchable metadata",
            "asset_index": "Indexing document in Elasticsearch",
        }
        stage = next((label for key, label, _ in PIPELINE_STAGES if key == job.job_type), job.job_type)
        started = self._aware(job.claimed_at or job.updated_at)
        return {
            "stage": stage, "job_type": job.job_type, "status": job.status,
            "filename": filename, "provider": job.provider_key, "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts, "started_at": started,
            "elapsed_ms": max(0, int((now - started).total_seconds() * 1000)) if started else None,
            "message": labels.get(job.job_type, "Processing pipeline work"),
        }

    def _failures(self, tenant_id: str) -> list[dict[str, Any]]:
        latest = self._latest_jobs(tenant_id)
        rows = self.session.execute(select(
            latest.c.job_type, latest.c.last_error_code, func.count(),
            func.max(latest.c.updated_at),
        ).where(
            latest.c.status == JobStatus.FAILED.value,
        ).group_by(latest.c.job_type, latest.c.last_error_code).order_by(
            func.max(latest.c.updated_at).desc(),
        ).limit(50)).all()
        labels = {key: label for key, label, _ in PIPELINE_STAGES}
        return [{
            "stage": labels.get(job_type, job_type),
            "error_code": error_code or "processing_failed",
            "message": (error_code or "processing_failed").replace("_", " "),
            "count": int(count), "latest_at": latest_at,
        } for job_type, error_code, count, latest_at in rows]

    @staticmethod
    def _asset_stage_statuses(state: str) -> dict[str, str]:
        order = {
            "discovered": 0, "download_pending": 0, "downloading": 0,
            "downloaded": 1, "duplicate_detected": 1, "storage_pending": 1,
            "stored": 2, "analysis_pending": 2, "analyzing": 2,
            "metadata_ready": 3, "projection_pending": 3,
            "projection_ready": 4, "search_pending": 4,
            "indexed": 5, "completed": 5,
        }
        failures = {
            "download_failed": "download", "storage_failed": "store",
            "analysis_failed": "analyze", "projection_failed": "projection",
            "search_failed": "index",
        }
        stages = ("download", "store", "analyze", "projection", "index")
        result = {stage: "not_started" for stage in stages}
        if state in failures:
            result[failures[state]] = "failed"
            return result
        completed_through = order.get(state, 0)
        for position, stage in enumerate(stages, start=1):
            if position <= completed_through:
                result[stage] = "completed"
        pending = {
            "download_pending": "download", "storage_pending": "store",
            "analysis_pending": "analyze", "projection_pending": "projection",
            "search_pending": "index",
        }
        processing = {"downloading": "download", "analyzing": "analyze"}
        if state in pending:
            result[pending[state]] = "pending"
        elif state in processing:
            result[processing[state]] = "processing"
        return result

    def _asset_progress(self, tenant_id: str, supported: int) -> list[dict[str, Any]]:
        rows = self.session.execute(select(
            AssetPipelineModel.state, func.count(AssetPipelineModel.id),
        ).join(
            SourceAssetModel,
            (SourceAssetModel.tenant_id == AssetPipelineModel.tenant_id)
            & (SourceAssetModel.id == AssetPipelineModel.source_asset_id),
        ).where(
            AssetPipelineModel.tenant_id == tenant_id,
            SourceAssetModel.deleted_at.is_(None),
            SourceAssetModel.mime_type.in_(SUPPORTED_IMAGE_MIME_TYPES),
        ).group_by(AssetPipelineModel.state)).all()
        stages = ("discovered", "downloaded", "stored", "analyzed", "projection_built", "indexed")
        counts = {stage: 0 for stage in stages}
        counts["discovered"] = supported
        furthest = {
            "downloaded": "downloaded", "duplicate_detected": "downloaded",
            "storage_pending": "downloaded", "stored": "stored",
            "analysis_pending": "stored", "analyzing": "stored",
            "metadata_ready": "analyzed", "projection_pending": "analyzed",
            "projection_ready": "projection_built", "search_pending": "projection_built",
            "indexed": "indexed", "completed": "indexed",
        }
        for state, count in rows:
            stage = furthest.get(state)
            if stage:
                counts[stage] += int(count or 0)
        counts["discovered"] = max(0, counts["discovered"] - sum(counts[stage] for stage in stages[1:]))
        return [{"key": stage, "count": counts[stage]} for stage in stages]

    def _recent_assets(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(select(
            AssetPipelineModel, SourceAssetModel.filename,
        ).outerjoin(
            SourceAssetModel,
            (SourceAssetModel.tenant_id == AssetPipelineModel.tenant_id)
            & (SourceAssetModel.id == AssetPipelineModel.source_asset_id),
        ).where(
            AssetPipelineModel.tenant_id == tenant_id,
        ).order_by(
            AssetPipelineModel.updated_at.desc(), AssetPipelineModel.id.desc(),
        ).limit(50)).all()
        return [{
            "asset_id": pipeline.asset_id,
            "filename": filename or "Untitled source asset",
            "state": pipeline.state, "stage_statuses": self._asset_stage_statuses(pipeline.state),
            "updated_at": pipeline.updated_at, "error_code": pipeline.last_error_code,
        } for pipeline, filename in rows]

    def snapshot(self, tenant_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        stages = self._stage_counts(tenant_id, now)
        latest_sync = self._latest_source_sync(tenant_id)
        supported = int(self.session.scalar(select(func.count(SourceAssetModel.id)).where(
            SourceAssetModel.tenant_id == tenant_id,
            SourceAssetModel.deleted_at.is_(None),
            SourceAssetModel.mime_type.in_(SUPPORTED_IMAGE_MIME_TYPES),
        )) or 0)
        all_sources = int(self.session.scalar(select(func.count(SourceAssetModel.id)).where(
            SourceAssetModel.tenant_id == tenant_id,
            SourceAssetModel.deleted_at.is_(None),
        )) or 0)
        pipelines = self.session.execute(select(
            func.coalesce(func.sum(case((AssetPipelineModel.state.in_(("indexed", "completed")), 1), else_=0)), 0),
            func.coalesce(func.sum(case((AssetPipelineModel.state.in_(("downloading", "analyzing")), 1), else_=0)), 0),
            func.coalesce(func.sum(case((AssetPipelineModel.state.like("%_pending"), 1), else_=0)), 0),
            func.coalesce(func.sum(case((AssetPipelineModel.state.like("%_failed"), 1), else_=0)), 0),
        ).where(AssetPipelineModel.tenant_id == tenant_id)).one()
        indexed, active, queued, failed = map(lambda value: int(value or 0), pipelines)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        throughput_today = int(self.session.scalar(select(func.count(ProcessingJobModel.id)).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type.in_([stage[0] for stage in PIPELINE_STAGES]),
            ProcessingJobModel.status == JobStatus.COMPLETED.value,
            ProcessingJobModel.completed_at >= today,
        )) or 0)
        total_stage_failed = sum(stage["failed"] for stage in stages.values())
        return {
            "generated_at": now,
            "latest_source_sync": latest_sync,
            "overall": {
                "source_items_discovered": all_sources,
                "supported_assets": supported,
                "unsupported_assets": max(0, all_sources - supported),
                "completed": indexed,
                "active": active,
                "queued": queued,
                "failed": max(failed, total_stage_failed),
                "skipped": 0,
                "indexed_percentage": round((indexed / supported) * 100, 1) if supported else None,
                "throughput_today": throughput_today,
                "asset_progress": self._asset_progress(tenant_id, supported),
            },
            "stages": list(stages.values()),
            "active_job": self._active_job(tenant_id, now),
            "failure_groups": self._failures(tenant_id),
            "recent_assets": self._recent_assets(tenant_id),
        }
