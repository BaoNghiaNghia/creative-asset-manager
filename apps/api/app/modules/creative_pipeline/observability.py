"""Read-only operational evidence for Creative Pipeline.

The pipeline tables and processing jobs are authoritative. This module turns
that durable state into a bounded tenant-scoped diagnostic; it deliberately
does not infer success from storage or mutate recovery state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.creative_pipeline.model import ArtifactModel, NodeRunModel, PipelineRunModel
from app.modules.processing.model import ProcessingJobModel

_JOB_TYPES = ("creative_pipeline_node", "creative_pipeline_scan")
_ACTIVE_JOB_STATUSES = ("pending", "processing", "retry")
_TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")


def _counts(session: Session, column, statement) -> dict[str, int]:
    return {str(key): int(value) for key, value in session.execute(
        statement.with_only_columns(column, func.count()).group_by(column)
    )}


def _percentile(values: list[int], ratio: float) -> int | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(ratio * len(values)) - 1)]


class CreativePipelineObservabilityService:
    """Returns bounded, secret-free diagnostics for exactly one tenant."""

    def __init__(self, session: Session, *, recent_limit: int = 25):
        self.session = session
        self.recent_limit = max(1, min(int(recent_limit), 100))

    def snapshot(self, tenant_id: str, *, now: datetime | None = None) -> dict[str, object]:
        now = now or datetime.now(timezone.utc)
        recent_since = now - timedelta(hours=24)
        runs = select(PipelineRunModel).where(PipelineRunModel.tenant_id == tenant_id)
        nodes = select(NodeRunModel).where(NodeRunModel.tenant_id == tenant_id)
        jobs = select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type.in_(_JOB_TYPES),
        )
        artifacts = select(ArtifactModel).where(ArtifactModel.tenant_id == tenant_id)

        run_by_status = _counts(self.session, PipelineRunModel.status, runs)
        node_by_status = _counts(self.session, NodeRunModel.status, nodes)
        node_by_type_status = [
            {"node_type": node_type, "status": status, "count": int(count)}
            for node_type, status, count in self.session.execute(
                nodes.with_only_columns(NodeRunModel.node_type, NodeRunModel.status, func.count())
                .group_by(NodeRunModel.node_type, NodeRunModel.status)
                .order_by(NodeRunModel.node_type, NodeRunModel.status)
            )
        ]
        job_by_status = _counts(self.session, ProcessingJobModel.status, jobs)
        artifact_by_status = _counts(self.session, ArtifactModel.status, artifacts)
        scan_by_status = _counts(self.session, ProcessingJobModel.status,
            jobs.where(ProcessingJobModel.job_type == "creative_pipeline_scan"))
        durations = [int(value) for value in self.session.scalars(
            jobs.with_only_columns(ProcessingJobModel.processing_duration_ms)
            .where(ProcessingJobModel.processing_duration_ms > 0)
            .order_by(ProcessingJobModel.updated_at.desc()).limit(500)
        )]

        failed_nodes = self.session.execute(
            nodes.where(NodeRunModel.last_error_code.is_not(None)).with_only_columns(
                NodeRunModel.id, NodeRunModel.pipeline_run_id, NodeRunModel.node_type,
                NodeRunModel.status, NodeRunModel.attempt_count, NodeRunModel.max_attempts,
                NodeRunModel.last_error_code, NodeRunModel.updated_at,
            ).order_by(NodeRunModel.updated_at.desc()).limit(self.recent_limit)
        )
        failures = [{
            "kind": "node", "node_id": row.id, "pipeline_run_id": row.pipeline_run_id,
            "node_type": row.node_type, "status": row.status,
            "attempt_count": row.attempt_count, "max_attempts": row.max_attempts,
            "error_code": row.last_error_code, "updated_at": row.updated_at,
        } for row in failed_nodes]
        failed_jobs = self.session.execute(
            jobs.where(ProcessingJobModel.last_error_code.is_not(None)).with_only_columns(
                ProcessingJobModel.id, ProcessingJobModel.job_type, ProcessingJobModel.entity_id,
                ProcessingJobModel.status, ProcessingJobModel.attempt_count,
                ProcessingJobModel.max_attempts, ProcessingJobModel.last_error_code,
                ProcessingJobModel.updated_at,
            ).order_by(ProcessingJobModel.updated_at.desc()).limit(self.recent_limit)
        )
        failures.extend({
            "kind": "job", "job_id": row.id, "job_type": row.job_type, "entity_id": row.entity_id,
            "status": row.status, "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts, "error_code": row.last_error_code,
            "updated_at": row.updated_at,
        } for row in failed_jobs)
        failures.sort(key=lambda item: item["updated_at"], reverse=True)

        completed_recently = int(self.session.scalar(
            runs.where(PipelineRunModel.status == "completed", PipelineRunModel.completed_at >= recent_since)
            .with_only_columns(func.count())
        ) or 0)
        terminal = sum(run_by_status.get(status, 0) for status in _TERMINAL_RUN_STATUSES)
        return {
            "tenant_id": tenant_id, "generated_at": now,
            "window": {"recent_since": recent_since, "recent_hours": 24},
            "runs": {"total": sum(run_by_status.values()), "by_status": run_by_status,
                     "terminal_total": terminal, "completed_last_24h": completed_recently},
            "nodes": {"total": sum(node_by_status.values()), "by_status": node_by_status,
                      "by_type_status": node_by_type_status},
            "jobs": {
                "total": sum(job_by_status.values()), "by_status": job_by_status,
                "active_total": sum(job_by_status.get(status, 0) for status in _ACTIVE_JOB_STATUSES),
                "retry_total": job_by_status.get("retry", 0), "scan_by_status": scan_by_status,
                "processing_duration_ms": {"sample_count": len(durations), "p50": _percentile(durations, .50),
                                           "p95": _percentile(durations, .95),
                                           "max": max(durations) if durations else None},
            },
            "artifacts": {"total": sum(artifact_by_status.values()), "by_status": artifact_by_status},
            "recent_failures": failures[:self.recent_limit],
        }
