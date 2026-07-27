from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, exists, func, literal, or_, select

from app.modules.ai_batch.model import AiBatchItemModel, AiBatchJobModel
from app.modules.ai_governance.model import AiBudgetReservationModel, AiUsageRecordModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_operations.repository import AiOperationsRepository as BaseRepository
from app.modules.ai_operations.schema import AI_JOB_TYPES, AiOperationsFilters
from app.domain.processing.types import JobStatus
from app.modules.processing.model import (
    PROCESSING_JOB_QUEUED_STATUSES, PROCESSING_JOB_RUNNING_STATUSES,
    PROCESSING_JOB_TERMINAL_STATUSES, ProcessingJobModel,
)


DEFERRED_AI_REASON_CODES = frozenset({"gemini_quota_deferred"})


class AiOperationsRepository(BaseRepository):
    def providers(self, f: AiOperationsFilters) -> list[dict[str, Any]]:
        p95_latency = (
            func.percentile_cont(0.95).within_group(AiUsageRecordModel.latency_ms)
            if self.session.get_bind().dialect.name == "postgresql"
            else func.max(AiUsageRecordModel.latency_ms)
        )
        statement = self._usage_select(
            AiUsageRecordModel.provider, AiUsageRecordModel.model,
            AiUsageRecordModel.processing_mode, func.count(AiUsageRecordModel.id),
            func.coalesce(func.sum(case((AiUsageRecordModel.outcome == "completed", 1), else_=0)), 0),
            func.coalesce(func.sum(case((AiUsageRecordModel.outcome.in_(("provider_failed", "invalid_metadata")), 1), else_=0)), 0),
            func.coalesce(func.avg(AiUsageRecordModel.latency_ms), 0),
            func.coalesce(p95_latency, 0),
            func.coalesce(func.sum(AiUsageRecordModel.input_units), 0),
            func.coalesce(func.sum(AiUsageRecordModel.output_units), 0),
            func.coalesce(func.sum(AiUsageRecordModel.locally_estimated_cost_micros), 0),
            func.coalesce(func.sum(AiUsageRecordModel.provider_reported_cost_micros), 0),
            filters=f,
        ).group_by(
            AiUsageRecordModel.provider, AiUsageRecordModel.model,
            AiUsageRecordModel.processing_mode,
        ).order_by(
            AiUsageRecordModel.provider, AiUsageRecordModel.model,
            AiUsageRecordModel.processing_mode,
        )
        reservations = self.session.execute(self._reservation_select(
            AiBudgetReservationModel.provider, AiBudgetReservationModel.model,
            AiBudgetReservationModel.processing_mode,
            func.coalesce(func.sum(AiBudgetReservationModel.actual_cost_micros), 0),
            filters=f,
        ).group_by(
            AiBudgetReservationModel.provider, AiBudgetReservationModel.model,
            AiBudgetReservationModel.processing_mode,
        )).all()
        reconciled = {(p, m, mode): int(cost) for p, m, mode, cost in reservations}
        result = []
        for row in self.session.execute(statement):
            provider, model, mode = row[:3]
            count, completed, failed = map(int, row[3:6])
            denominator = completed + failed
            result.append({
                "provider": provider, "model": model, "processing_mode": mode,
                "count": count, "completed": completed, "failed": failed,
                "success_rate": completed / denominator if denominator else 0.0,
                "average_latency_ms": float(row[6]),
                "input_units": int(row[7]), "output_units": int(row[8]),
                "estimated_cost_micros": int(row[9]),
                "provider_reported_cost_micros": int(row[10]),
                "reconciled_cost_micros": reconciled.get((provider, model, mode), 0),
                "currency": "USD",
            })
        return result

    def _job_analysis_match(self, f: AiOperationsFilters):
        terms = [
            AssetAiAnalysisModel.tenant_id == f.tenant_id,
            AssetAiAnalysisModel.id == ProcessingJobModel.entity_id,
        ]
        if f.model:
            terms.append(AssetAiAnalysisModel.ai_model == f.model)
        if f.processing_mode == "batch":
            terms.append(AssetAiAnalysisModel.pipeline_version.like("batch%"))
        elif f.processing_mode == "single":
            terms.append(~AssetAiAnalysisModel.pipeline_version.like("batch%"))
        if f.metadata_profile:
            terms.append(AssetAiAnalysisModel.metadata_profile == f.metadata_profile)
        source = self._source_exists(AssetAiAnalysisModel.asset_id, f)
        if source is not None:
            terms.append(source)
        return exists(select(literal(1)).where(*terms))

    def _job_batch_match(self, f: AiOperationsFilters):
        terms = [
            AiBatchJobModel.tenant_id == f.tenant_id,
            AiBatchJobModel.id == ProcessingJobModel.entity_id,
        ]
        if f.model:
            terms.append(AiBatchJobModel.model == f.model)
        if f.processing_mode:
            terms.append(literal(f.processing_mode) == "batch")
        if f.metadata_profile:
            terms.append(AiBatchJobModel.metadata_profile == f.metadata_profile)
        if f.source_provider:
            terms.append(exists(
                select(literal(1)).select_from(AiBatchItemModel)
                .join(AssetAiAnalysisModel, AssetAiAnalysisModel.id == AiBatchItemModel.analysis_id)
                .where(
                    AiBatchItemModel.batch_job_id == AiBatchJobModel.id,
                    self._source_exists(AssetAiAnalysisModel.asset_id, f),
                )
            ))
        return exists(select(literal(1)).where(*terms))

    def _job_scope_conditions(self, f: AiOperationsFilters):
        conditions = [
            ProcessingJobModel.tenant_id == f.tenant_id,
            ProcessingJobModel.job_type.in_(AI_JOB_TYPES),
        ]
        if f.provider:
            conditions.append(ProcessingJobModel.provider_key == f.provider)
        if f.model or f.processing_mode or f.metadata_profile or f.source_provider:
            conditions.append(or_(self._job_analysis_match(f), self._job_batch_match(f)))
        return conditions

    @staticmethod
    def _deferred_condition(status, job_type, error_code, next_attempt_at, now: datetime):
        return (
            status.in_(PROCESSING_JOB_QUEUED_STATUSES)
            & (job_type == "asset_analyze")
            & error_code.in_(DEFERRED_AI_REASON_CODES)
            & next_attempt_at.is_not(None)
            & (next_attempt_at > now)
        )

    def _job_status_condition(
        self,
        f: AiOperationsFilters,
        *,
        status,
        job_type,
        error_code,
        next_attempt_at,
        now: datetime,
    ):
        deferred = self._deferred_condition(status, job_type, error_code, next_attempt_at, now)
        if f.status == "waiting":
            return deferred
        if f.status == "queued":
            return status.in_(PROCESSING_JOB_QUEUED_STATUSES) & ~deferred & (
                next_attempt_at.is_(None) | (next_attempt_at <= now)
            )
        if f.status == "running":
            return status.in_(PROCESSING_JOB_RUNNING_STATUSES)
        if f.status == "retrying":
            return status == JobStatus.RETRY.value
        if f.status:
            return status == f.status
        return literal(True)

    def _latest_relevant_jobs(self, f: AiOperationsFilters):
        ranked = select(
            ProcessingJobModel.id.label("job_id"),
            func.row_number().over(
                partition_by=(
                    ProcessingJobModel.tenant_id,
                    ProcessingJobModel.job_type,
                    ProcessingJobModel.entity_type,
                    ProcessingJobModel.entity_id,
                ),
                order_by=(ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc()),
            ).label("position"),
        ).where(*self._job_scope_conditions(f)).subquery()
        return select(ProcessingJobModel).join(
            ranked, ranked.c.job_id == ProcessingJobModel.id,
        ).where(ranked.c.position == 1).subquery()

    def _job_conditions(self, f: AiOperationsFilters, *, failure_only=False):
        now = datetime.now(timezone.utc)
        conditions = self._job_scope_conditions(f) + [
            ProcessingJobModel.created_at >= f.from_at,
            ProcessingJobModel.created_at < f.to_at,
            self._job_status_condition(
                f,
                status=ProcessingJobModel.status,
                job_type=ProcessingJobModel.job_type,
                error_code=ProcessingJobModel.last_error_code,
                next_attempt_at=ProcessingJobModel.next_attempt_at,
                now=now,
            ),
        ]
        if failure_only:
            conditions.append(ProcessingJobModel.status == JobStatus.FAILED.value)
        return conditions

    def summary(self, f: AiOperationsFilters) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        latest = self._latest_relevant_jobs(f)
        terminal_at = func.coalesce(latest.c.completed_at, latest.c.updated_at)
        created_in_period = (
            (latest.c.created_at >= f.from_at)
            & (latest.c.created_at < f.to_at)
        )
        terminal_in_period = (
            latest.c.status.in_(PROCESSING_JOB_TERMINAL_STATUSES)
            & (terminal_at >= f.from_at)
            & (terminal_at < f.to_at)
        )
        deferred = self._deferred_condition(
            latest.c.status, latest.c.job_type, latest.c.last_error_code,
            latest.c.next_attempt_at, now,
        )
        visible_status = self._job_status_condition(
            f,
            status=latest.c.status,
            job_type=latest.c.job_type,
            error_code=latest.c.last_error_code,
            next_attempt_at=latest.c.next_attempt_at,
            now=now,
        )
        queued = (
            latest.c.status.in_(PROCESSING_JOB_QUEUED_STATUSES)
            & ~deferred
            & (latest.c.next_attempt_at.is_(None) | (latest.c.next_attempt_at <= now))
        )
        blocked = select(func.count(AssetAiAnalysisModel.id)).where(
            *self._analysis_conditions(f),
            AssetAiAnalysisModel.status == "budget_blocked",
        ).scalar_subquery()
        cancelled = select(func.count(AssetAiAnalysisModel.id)).where(
            *self._analysis_conditions(f),
            AssetAiAnalysisModel.processing_stage == "cancelled",
        ).scalar_subquery()
        row = self.session.execute(select(
            func.coalesce(func.sum(case((created_in_period & visible_status, 1), else_=0)), 0),
            # Queue/running cards describe the tenant's live work state. They
            # must not disappear merely because a job was created before the
            # selected reporting period; historical cards remain period-bounded.
            func.coalesce(func.sum(case((visible_status & queued, 1), else_=0)), 0),
            func.coalesce(func.sum(case((
                visible_status
                & latest.c.status.in_(PROCESSING_JOB_RUNNING_STATUSES), 1
            ), else_=0)), 0),
            func.coalesce(func.sum(case((
                terminal_in_period
                & visible_status
                & (latest.c.status == JobStatus.COMPLETED.value), 1
            ), else_=0)), 0),
            func.coalesce(func.sum(case((
                terminal_in_period
                & visible_status
                & (latest.c.status == JobStatus.FAILED.value), 1
            ), else_=0)), 0),
            func.coalesce(func.sum(case((visible_status & deferred, 1), else_=0)), 0),
            func.min(case((visible_status & deferred, latest.c.next_attempt_at), else_=None)),
            func.coalesce(blocked, 0),
            func.coalesce(cancelled, 0),
        ).select_from(latest)).one()
        requested, queued_count, running, completed, failed, deferred_count, next_retry, blocked_count, cancelled_count = row
        requested = int(requested or 0)
        queued_count = int(queued_count or 0)
        running = int(running or 0)
        completed = int(completed or 0)
        failed = int(failed or 0)
        deferred_count = int(deferred_count or 0)
        blocked_count = int(blocked_count or 0)
        cancelled_count = int(cancelled_count or 0)
        usage = self.session.execute(self._usage_select(
            func.coalesce(func.sum(AiUsageRecordModel.input_units), 0),
            func.coalesce(func.sum(AiUsageRecordModel.output_units), 0),
            func.coalesce(func.sum(AiUsageRecordModel.locally_estimated_cost_micros), 0),
            func.coalesce(func.sum(AiUsageRecordModel.provider_reported_cost_micros), 0),
            filters=f,
        )).one()
        input_units, output_units, estimated, provider_reported = map(int, usage)
        reconciled = self._reconciled_cost(f)
        denominator = completed + failed
        return {
            "period": {"from": f.from_at, "to": f.to_at},
            "requested": requested, "queued": queued_count, "running": running,
            "completed": completed, "failed": failed, "cancelled": cancelled_count,
            "budget_blocked": blocked_count, "deferred": deferred_count,
            "next_deferred_retry_at": next_retry,
            "success_rate": (completed / denominator) if denominator else 0.0,
            "input_units": input_units, "output_units": output_units,
            "cost": {
                "estimated_cost_micros": estimated,
                "provider_reported_cost_micros": provider_reported,
                "reconciled_cost_micros": reconciled,
                "currency": "USD",
            },
            "latency": self._latency(f),
            "average_cost_per_completed_asset_micros": reconciled / completed if completed else 0.0,
        }

    def failures(self, f: AiOperationsFilters) -> list[dict[str, Any]]:
        result: dict[tuple[str, str], int] = defaultdict(int)
        conditions = self._analysis_conditions(f) + [
            AssetAiAnalysisModel.status == "failed",
            AssetAiAnalysisModel.last_error_code.is_not(None),
        ]
        for code, count in self.session.execute(select(
            AssetAiAnalysisModel.last_error_code, func.count()
        ).where(*conditions).group_by(AssetAiAnalysisModel.last_error_code)):
            result[("analysis", str(code))] += int(count)
        for code, count in self.session.execute(select(
            ProcessingJobModel.last_error_code, func.count()
        ).where(
            *self._job_conditions(f, failure_only=True),
            ProcessingJobModel.last_error_code.is_not(None),
        ).group_by(ProcessingJobModel.last_error_code)):
            result[("processing_job", str(code))] += int(count)
        return [
            {"source": source, "error_code": code, "count": count}
            for (source, code), count in sorted(
                result.items(), key=lambda item: (-item[1], item[0])
            )
        ]

    def jobs(self, f: AiOperationsFilters, *, page: int, page_size: int) -> dict[str, Any]:
        conditions = self._job_conditions(f)
        total = int(self.session.scalar(
            select(func.count()).select_from(ProcessingJobModel).where(*conditions)
        ) or 0)
        rows = self.session.scalars(
            select(ProcessingJobModel).where(*conditions)
            .order_by(ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()
        now = datetime.now(timezone.utc)
        def is_deferred(job: ProcessingJobModel) -> bool:
            retry_at = job.next_attempt_at
            if retry_at is not None and retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return bool(
                job.status == "pending"
                and job.last_error_code in DEFERRED_AI_REASON_CODES
                and retry_at is not None
                and retry_at > now
            )
        return {
            "page": page, "page_size": page_size, "total": total,
            "items": [{
                "id": job.id, "job_type": job.job_type,
                "entity_type": job.entity_type, "entity_id": job.entity_id,
                "provider": job.provider_key, "status": job.status,
                "priority": job.priority, "attempt_count": job.attempt_count,
                "max_attempts": job.max_attempts,
                "processing_duration_ms": job.processing_duration_ms,
                "next_attempt_at": job.next_attempt_at,
                "is_deferred": is_deferred(job),
                "waiting_reason": job.last_error_code if is_deferred(job) else None,
                "claimed_at": job.claimed_at,
                "lease_expires_at": job.lease_expires_at,
                "created_at": job.created_at, "updated_at": job.updated_at,
                "completed_at": job.completed_at,
                "error": None if not job.last_error_code else {
                    "code": job.last_error_code,
                    "retryable": job.status == "retry",
                },
            } for job in rows],
        }

    def usage(self, f: AiOperationsFilters, *, page: int, page_size: int) -> dict[str, Any]:
        total = int(self.session.scalar(self._usage_select(
            func.count(AiUsageRecordModel.id), filters=f
        )) or 0)
        rows = self.session.scalars(
            self._usage_select(AiUsageRecordModel, filters=f)
            .order_by(AiUsageRecordModel.occurred_at.desc(), AiUsageRecordModel.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()
        return {
            "page": page, "page_size": page_size, "total": total,
            "items": [{
                "id": item.id, "asset_id": item.asset_id,
                "analysis_id": item.analysis_id, "job_id": item.job_id,
                "provider": item.provider, "model": item.model,
                "processing_mode": item.processing_mode,
                "metadata_profile": item.metadata_profile,
                "metadata_profile_version": item.metadata_profile_version,
                "input_units": item.input_units,
                "output_units": item.output_units,
                "media_units": item.media_units,
                "estimated_cost_micros": item.locally_estimated_cost_micros,
                "provider_reported_cost_micros": item.provider_reported_cost_micros,
                "currency": item.currency, "latency_ms": item.latency_ms,
                "outcome": item.outcome, "retry_count": item.retry_count,
                "occurred_at": item.occurred_at,
            } for item in rows],
        }

    def iter_jobs(self, f: AiOperationsFilters, *, row_limit: int):
        statement = (
            select(ProcessingJobModel)
            .where(*self._job_conditions(f))
            .order_by(ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc())
            .limit(row_limit)
            .execution_options(yield_per=500)
        )
        for job in self.session.scalars(statement):
            yield {
                "id": job.id, "job_type": job.job_type,
                "entity_type": job.entity_type, "entity_id": job.entity_id,
                "provider": job.provider_key, "status": job.status,
                "attempt_count": job.attempt_count, "max_attempts": job.max_attempts,
                "processing_duration_ms": job.processing_duration_ms,
                "next_attempt_at": job.next_attempt_at,
                "created_at": job.created_at, "completed_at": job.completed_at,
                "error_code": job.last_error_code,
                "retryable": job.status == "retry",
            }

    def iter_usage(self, f: AiOperationsFilters, *, row_limit: int):
        statement = (
            self._usage_select(AiUsageRecordModel, filters=f)
            .order_by(AiUsageRecordModel.occurred_at.desc(), AiUsageRecordModel.id.desc())
            .limit(row_limit)
            .execution_options(yield_per=500)
        )
        for item in self.session.scalars(statement):
            yield {
                "id": item.id, "asset_id": item.asset_id,
                "analysis_id": item.analysis_id, "job_id": item.job_id,
                "provider": item.provider, "model": item.model,
                "processing_mode": item.processing_mode,
                "metadata_profile": item.metadata_profile,
                "input_units": item.input_units, "output_units": item.output_units,
                "media_units": item.media_units,
                "estimated_cost_micros": item.locally_estimated_cost_micros,
                "provider_reported_cost_micros": item.provider_reported_cost_micros,
                "currency": item.currency, "latency_ms": item.latency_ms,
                "outcome": item.outcome, "retry_count": item.retry_count,
                "occurred_at": item.occurred_at,
            }