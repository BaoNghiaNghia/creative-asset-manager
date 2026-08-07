from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, case, func, literal, select
from sqlalchemy.orm import Session

from app.domain.processing.types import JobStatus
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.processing.model import PROCESSING_JOB_QUEUED_STATUSES, PROCESSING_JOB_RUNNING_STATUSES, ProcessingJobModel
from app.modules.source_sync.model import SourceSyncRunModel
from app.modules.pipeline.mime_types import SUPPORTED_GOOGLE_DRIVE_IMAGE_MIME_TYPES, normalize_source_mime_type

PIPELINE_STAGES: tuple[tuple[str, str, str], ...] = (
    ("source_asset_download", "Download", "Download supported source images from Google Drive."),
    ("asset_store", "Store", "Save asset content and source linkage."),
    ("asset_analyze", "AI Analyze", "Generate metadata for imported assets."),
    ("search_projection_build", "Search Projection", "Build searchable metadata."),
    ("asset_index", "Elasticsearch Index", "Index the search document."),
)
SUPPORTED_IMAGE_MIME_TYPES = tuple(SUPPORTED_GOOGLE_DRIVE_IMAGE_MIME_TYPES)
_DEFERRED_CODES = {"ai_model_rate_limited", "gemini_quota_deferred"}
_SKIPPED_CODES = {"unsupported_source_mime_type": "unsupported", "source_content_too_large": "oversized"}
_ACTIONABLE_CODES = {"analysis_image_dimensions": "preprocessing_required", "gemini_http_error": "provider_error", "search_index_unconfigured": "configuration_error"}
_STAGE_POSITION = {"source_asset_download": 1, "asset_store": 2, "asset_analyze": 3, "search_projection_build": 4, "asset_index": 5}
_STATE_POSITION = {"discovered": 0, "download_pending": 0, "downloading": 0, "downloaded": 1, "duplicate_detected": 1, "storage_pending": 1, "stored": 2, "analysis_pending": 2, "analyzing": 2, "metadata_ready": 3, "projection_pending": 3, "projection_ready": 4, "search_pending": 4, "indexed": 5, "sidecar_pending": 5, "completed": 5}
_FAILURE_STAGE = {"download_failed": "source_asset_download", "storage_failed": "asset_store", "analysis_failed": "asset_analyze", "projection_failed": "search_projection_build", "search_failed": "asset_index"}


class PipelineOperationsRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value

    def _active_source_ids(self, tenant_id: str) -> tuple[list[str], int]:
        """A duplicate source is excluded only when it was explicitly decommissioned."""
        sources = self.session.execute(select(ExternalSourceModel.id, ExternalSourceModel.source_metadata).where(ExternalSourceModel.tenant_id == tenant_id)).all()
        active, excluded = [], 0
        for source_id, metadata in sources:
            metadata = metadata if isinstance(metadata, dict) else {}
            canonical = metadata.get("canonical_source_id")
            if metadata.get("decommissioned_at") or (canonical and canonical != source_id):
                excluded += 1
            else:
                active.append(source_id)
        return active, excluded

    def _logical_assets(self, tenant_id: str, source_ids: list[str]):
        impossible = SourceAssetModel.id == literal("__no_active_source__")
        return select(
            SourceAssetModel.id.label("logical_id"), SourceAssetModel.filename.label("filename"),
            SourceAssetModel.mime_type.label("mime_type"), SourceAssetModel.updated_at.label("source_updated_at"),
            AssetPipelineModel.id.label("pipeline_id"), AssetPipelineModel.asset_id.label("asset_id"),
            AssetPipelineModel.state.label("pipeline_state"), AssetPipelineModel.last_error_code.label("pipeline_error_code"),
            AssetPipelineModel.last_error_message.label("pipeline_error_message"), AssetPipelineModel.updated_at.label("pipeline_updated_at"),
        ).select_from(SourceAssetModel).outerjoin(AssetPipelineModel, and_(
            AssetPipelineModel.tenant_id == SourceAssetModel.tenant_id,
            AssetPipelineModel.source_asset_id == SourceAssetModel.id,
        )).where(
            SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.deleted_at.is_(None),
            SourceAssetModel.mime_type.in_(SUPPORTED_IMAGE_MIME_TYPES),
            SourceAssetModel.external_source_id.in_(source_ids) if source_ids else impossible,
        ).cte("logical_assets")

    @staticmethod
    def _skip_category(code: str | None, message: str | None) -> str | None:
        if code in _SKIPPED_CODES:
            return _SKIPPED_CODES[code]
        if code == "InvalidPipelineContent" and "byte" in (message or "").lower() and "limit" in (message or "").lower():
            return "oversized"
        return None

    @staticmethod
    def _failure_category(code: str | None) -> str:
        return _ACTIONABLE_CODES.get(code or "", "other_actionable")

    def _latest_stage_job(self, logical, tenant_id: str, stage: str):
        job = ProcessingJobModel
        condition = and_(job.entity_type == "source_asset", job.entity_id == logical.c.logical_id) if stage == "source_asset_download" else and_(job.entity_type == "asset_pipeline", job.entity_id == logical.c.pipeline_id)
        ranked = select(
            logical.c.logical_id.label("logical_id"), job.id.label("job_id"), job.status.label("job_status"),
            job.next_attempt_at.label("next_attempt_at"), job.last_error_code.label("error_code"),
            job.last_error_message.label("error_message"), job.updated_at.label("job_updated_at"),
            func.row_number().over(partition_by=logical.c.logical_id, order_by=(job.created_at.desc(), job.updated_at.desc(), job.id.desc())).label("rn"),
        ).select_from(logical.join(job, and_(job.tenant_id == tenant_id, job.job_type == stage, condition))).subquery()
        return select(ranked).where(ranked.c.rn == 1).subquery()

    def _stage_rows(self, tenant_id: str, logical, now: datetime) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for key, label, subtitle in PIPELINE_STAGES:
            latest = self._latest_stage_job(logical, tenant_id, key)
            rows = self.session.execute(select(
                logical.c.logical_id, logical.c.pipeline_state, logical.c.pipeline_error_code, logical.c.pipeline_error_message,
                latest.c.job_status, latest.c.next_attempt_at, latest.c.error_code, latest.c.error_message,
            ).select_from(logical.outerjoin(latest, latest.c.logical_id == logical.c.logical_id))).all()
            counts = {name: 0 for name in ("total_logical_assets", "completed_assets", "queued_assets", "eligible_now_assets", "waiting_assets", "processing_assets", "needs_attention_assets", "skipped_assets", "not_started_assets")}
            position = _STAGE_POSITION[key]
            for logical_id, state, pcode, pmessage, status, next_at, jcode, jmessage in rows:
                counts["total_logical_assets"] += 1
                state_position, state_failure = _STATE_POSITION.get(state or "discovered", 0), _FAILURE_STAGE.get(state or "") == key
                code, message = (jcode or pcode), (jmessage or pmessage)
                if state_position >= position:
                    effective = "completed"
                elif status in PROCESSING_JOB_RUNNING_STATUSES:
                    effective = "processing"
                elif status == JobStatus.FAILED.value or state_failure:
                    effective = "skipped" if self._skip_category(code, message) else "needs_attention"
                elif status == JobStatus.RETRY.value or (status in PROCESSING_JOB_QUEUED_STATUSES and (code in _DEFERRED_CODES or (next_at is not None and self._aware(next_at) and self._aware(next_at) > now))):
                    effective = "waiting"
                elif status in PROCESSING_JOB_QUEUED_STATUSES:
                    effective = "queued"
                else:
                    effective = "not_started"
                counts[effective + "_assets"] += 1
                results.append({"logical_id": logical_id, "stage": key, "state": effective, "error_code": code, "error_message": message, "pipeline_state": state})
            raw = self.session.execute(select(
                func.count(ProcessingJobModel.id),
                func.coalesce(func.sum(case((ProcessingJobModel.status == JobStatus.COMPLETED.value, 1), else_=0)), 0),
                func.coalesce(func.sum(case((ProcessingJobModel.status == JobStatus.FAILED.value, 1), else_=0)), 0),
            ).where(ProcessingJobModel.tenant_id == tenant_id, ProcessingJobModel.job_type == key)).one()
            denominator = counts["total_logical_assets"]
            results.append({"stage_summary": {"key": key, "label": label, "subtitle": subtitle, **counts,
                "percentage": round(100 * counts["completed_assets"] / denominator, 1) if denominator else None,
                "total_attempts": int(raw[0] or 0), "completed_attempts": int(raw[1] or 0), "failed_attempts": int(raw[2] or 0),
                # legacy aliases are intentionally retained for rolling frontend deployments.
                "total": denominator, "pending": counts["queued_assets"] + counts["waiting_assets"], "eligible_now": counts["eligible_now_assets"],
                "waiting": counts["waiting_assets"], "processing": counts["processing_assets"], "completed": counts["completed_assets"], "failed": counts["needs_attention_assets"],
            }})
        return results

    def _latest_source_sync(self, tenant_id: str) -> dict[str, Any] | None:
        run = self.session.scalar(select(SourceSyncRunModel).where(SourceSyncRunModel.tenant_id == tenant_id).order_by(SourceSyncRunModel.started_at.desc(), SourceSyncRunModel.updated_at.desc(), SourceSyncRunModel.id.desc()).limit(1))
        if run is None:
            return None
        finished, started = self._aware(run.completed_at or run.failed_at), self._aware(run.started_at)
        return {"mode": run.mode, "status": run.status, "pages_count": run.pages_count, "items_seen_count": run.items_seen_count, "jobs_created_count": run.jobs_created_count, "started_at": run.started_at, "completed_at": run.completed_at, "duration_ms": max(0, int((finished - started).total_seconds() * 1000)) if finished and started else None, "error_code": (run.error_json or {}).get("code") if isinstance(run.error_json, dict) else None}

    def _active_job(self, tenant_id: str, now: datetime) -> dict[str, Any] | None:
        job = self.session.scalar(select(ProcessingJobModel).where(ProcessingJobModel.tenant_id == tenant_id, ProcessingJobModel.job_type.in_([item[0] for item in PIPELINE_STAGES]), ProcessingJobModel.status.in_(PROCESSING_JOB_RUNNING_STATUSES)).order_by(ProcessingJobModel.claimed_at.asc(), ProcessingJobModel.created_at.asc()).limit(1))
        if not job:
            return None
        filename = self.session.scalar(select(SourceAssetModel.filename).where(SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.id == job.entity_id)) if job.job_type == "source_asset_download" else None
        labels = {"source_asset_download": "Downloading from Google Drive", "asset_store": "Saving asset content and source linkage", "asset_analyze": "Analyzing metadata", "search_projection_build": "Building searchable metadata", "asset_index": "Indexing document in Elasticsearch"}
        label = next((value for key, value, _ in PIPELINE_STAGES if key == job.job_type), job.job_type)
        started = self._aware(job.claimed_at or job.updated_at)
        return {"stage": label, "job_type": job.job_type, "status": job.status, "filename": filename, "provider": job.provider_key, "attempt_count": job.attempt_count, "max_attempts": job.max_attempts, "started_at": started, "elapsed_ms": max(0, int((now - started).total_seconds() * 1000)) if started else None, "message": labels.get(job.job_type, "Processing pipeline work")}

    @staticmethod
    def _asset_stage_statuses(state: str) -> dict[str, str]:
        status = {key: "not_started" for key in _STAGE_POSITION}
        failure = _FAILURE_STAGE.get(state)
        if failure:
            status[failure] = "failed"
        else:
            position = _STATE_POSITION.get(state, 0)
            for key, ordinal in _STAGE_POSITION.items():
                if ordinal <= position:
                    status[key] = "completed"
            if state in {"downloading", "analyzing"}:
                status["source_asset_download" if state == "downloading" else "asset_analyze"] = "processing"
            pending = {"download_pending": "source_asset_download", "storage_pending": "asset_store", "analysis_pending": "asset_analyze", "projection_pending": "search_projection_build", "search_pending": "asset_index"}
            if state in pending:
                status[pending[state]] = "pending"
        return {"download": status["source_asset_download"], "store": status["asset_store"], "analyze": status["asset_analyze"], "projection": status["search_projection_build"], "index": status["asset_index"]}

    def _recent_assets(self, logical, *, page: int, page_size: int) -> dict[str, Any]:
        total = int(self.session.scalar(select(func.count()).select_from(logical)) or 0)
        rows = self.session.execute(select(logical).order_by(func.coalesce(logical.c.pipeline_updated_at, logical.c.source_updated_at).desc(), logical.c.logical_id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        return {"page": page, "page_size": page_size, "total": total, "items": [{"asset_id": row["asset_id"], "filename": row["filename"] or "Untitled source asset", "state": row["pipeline_state"] or "discovered", "stage_statuses": self._asset_stage_statuses(row["pipeline_state"] or "discovered"), "updated_at": row["pipeline_updated_at"] or row["source_updated_at"], "error_code": row["pipeline_error_code"]} for row in rows]}

    def snapshot(self, tenant_id: str, *, recent_page: int = 1, recent_page_size: int = 25) -> dict[str, Any]:
        recent_page, recent_page_size, now = max(1, recent_page), recent_page_size if recent_page_size in {25, 50, 100} else 25, datetime.now(timezone.utc)
        source_ids, decommissioned_sources = self._active_source_ids(tenant_id)
        logical = self._logical_assets(tenant_id, source_ids)
        stage_rows = self._stage_rows(tenant_id, logical, now)
        stages = [row["stage_summary"] for row in stage_rows if "stage_summary" in row]
        state_rows = [row for row in stage_rows if "stage" in row]
        by_asset: dict[str, list[dict[str, Any]]] = {}
        for row in state_rows:
            by_asset.setdefault(row["logical_id"], []).append(row)
        in_progress = sum(any(row["state"] == "processing" for row in rows) for rows in by_asset.values())
        queued = sum(any(row["state"] in {"queued", "waiting"} for row in rows) for rows in by_asset.values())
        attention = sum(any(row["state"] == "needs_attention" for row in rows) for rows in by_asset.values())
        skipped_stage = sum(any(row["state"] == "skipped" for row in rows) for rows in by_asset.values())
        supported = len(by_asset)
        progress = {"discovered": 0, "downloaded": 0, "stored": 0, "analyzed": 0, "projection_ready": 0, "search_ready": 0}
        for rows in by_asset.values():
            position = _STATE_POSITION.get(rows[0]["pipeline_state"] or "discovered", 0)
            key = "search_ready" if position >= 5 else "projection_ready" if position == 4 else "analyzed" if position == 3 else "stored" if position == 2 else "downloaded" if position == 1 else "discovered"
            progress[key] += 1
        source_filter = SourceAssetModel.external_source_id.in_(source_ids) if source_ids else SourceAssetModel.id == literal("__no_active_source__")
        all_active = int(self.session.scalar(select(func.count(SourceAssetModel.id)).where(SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.deleted_at.is_(None), source_filter)) or 0)
        image_active = int(self.session.scalar(select(func.count(SourceAssetModel.id)).where(SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.deleted_at.is_(None), source_filter, func.lower(SourceAssetModel.mime_type).like("image/%"))) or 0)
        unsupported_formats = max(0, image_active - supported)
        skipped = {"folders_non_images": max(0, all_active - image_active), "unsupported": unsupported_formats, "oversized": 0, "other_permanent": 0}
        failures: dict[tuple[str, str, str], dict[str, Any]] = {}
        for rows in by_asset.values():
            for row in rows:
                if row["state"] == "skipped":
                    skipped[self._skip_category(row["error_code"], row["error_message"]) or "other_permanent"] += 1
                if row["state"] == "needs_attention":
                    code = row["error_code"] or "processing_failed"; category = self._failure_category(code); key = (row["stage"], code, category)
                    item = failures.setdefault(key, {"stage": next(label for stage, label, _ in PIPELINE_STAGES if stage == row["stage"]), "error_code": code, "category": category, "message": code.replace("_", " "), "count": 0, "latest_at": now})
                    item["count"] += 1
        return {"generated_at": now, "definitions": {"snapshot": "Current logical asset state; reporting date filters never affect this endpoint.", "attempt_diagnostics": "Raw immutable processing-job attempts; diagnostics only."}, "latest_source_sync": self._latest_source_sync(tenant_id),
            "overall": {"source_items_discovered": all_active, "supported_assets": supported, "eligible_assets": supported, "unsupported_assets": skipped["unsupported"], "completed": progress["search_ready"], "search_ready_assets": progress["search_ready"], "active": in_progress, "in_progress_assets": in_progress, "queued": queued, "queued_assets": queued, "failed": attention, "needs_attention_assets": attention, "skipped": skipped["folders_non_images"] + skipped["unsupported"] + skipped_stage, "skipped_assets": skipped["folders_non_images"] + skipped["unsupported"] + skipped_stage, "indexed_percentage": round(progress["search_ready"] / supported * 100, 1) if supported else None, "throughput_today": 0, "asset_progress": [{"key": key, "count": count} for key, count in progress.items()]},
            "stages": stages, "active_job": self._active_job(tenant_id, now), "failure_groups": list(failures.values()), "skipped_breakdown": [{"category": key, "count": value} for key, value in skipped.items() if value], "recent_assets": self._recent_assets(logical, page=recent_page, page_size=recent_page_size),
            "diagnostics": {"decommissioned_sources_excluded": decommissioned_sources, "raw_attempts": {stage["key"]: {key: stage[key] for key in ("total_attempts", "completed_attempts", "failed_attempts")} for stage in stages}}}

    def validation_report(self, tenant_id: str) -> dict[str, Any]:
        snapshot = self.snapshot(tenant_id)
        total = sum(item["count"] for item in snapshot["overall"]["asset_progress"])
        eligible = snapshot["overall"]["eligible_assets"]
        return {"tenant_id": tenant_id, "generated_at": snapshot["generated_at"], "eligible_unique_assets": eligible, "furthest_stage_total": total, "duplicate_logical_assets": 0, "raw_attempts_vs_unique_assets": [{"stage": item["key"], "unique_assets": item["total_logical_assets"], "total_attempts": item["total_attempts"], "failed_attempts": item["failed_attempts"]} for item in snapshot["stages"]], "unresolved_actionable_assets": snapshot["overall"]["needs_attention_assets"], "skipped_assets_by_category": snapshot["skipped_breakdown"], "historical_failures_excluded": 0, "decommissioned_source_rows_excluded": snapshot["diagnostics"]["decommissioned_sources_excluded"], "invariant_violations": [] if total == eligible else [f"furthest-stage partition is {total}, expected {eligible}"]}
