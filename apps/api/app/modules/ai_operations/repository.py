from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from app.modules.ai_batch.model import AiBatchItemModel, AiBatchJobModel
from app.modules.ai_governance.model import AiBudgetReservationModel, AiUsageRecordModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_operations.schema import AI_JOB_TYPES, AiOperationsFilters
from app.modules.assets.model import AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.processing.model import ProcessingJobModel


DEFERRED_AI_REASON_CODES = frozenset({"gemini_quota_deferred"})


def _source_type(value: str) -> str:
    return value.strip().lower().replace("-", "_")


class AiOperationsRepository:
    """Bounded SQL aggregations for a tenant AI Operations dashboard."""

    def __init__(self, session: Session):
        self.session = session
        self.dialect = session.get_bind().dialect.name

    @staticmethod
    def _source_exists(asset_id, filters: AiOperationsFilters):
        if not filters.source_provider:
            return None
        return exists(
            select(literal(1))
            .select_from(AssetSourceLinkModel)
            .join(
                SourceAssetModel,
                (SourceAssetModel.id == AssetSourceLinkModel.source_asset_id)
                & (SourceAssetModel.tenant_id == AssetSourceLinkModel.tenant_id),
            )
            .join(
                ExternalSourceModel,
                (ExternalSourceModel.id == SourceAssetModel.external_source_id)
                & (ExternalSourceModel.tenant_id == SourceAssetModel.tenant_id),
            )
            .where(
                AssetSourceLinkModel.tenant_id == filters.tenant_id,
                AssetSourceLinkModel.asset_id == asset_id,
                ExternalSourceModel.source_type == _source_type(filters.source_provider),
            )
        )

    @staticmethod
    def _analysis_status(status: str | None):
        if not status:
            return None
        if status == "queued":
            return (
                (AssetAiAnalysisModel.status == "pending")
                & (func.coalesce(AssetAiAnalysisModel.processing_stage, "") != "cancelled")
            )
        if status == "cancelled":
            return AssetAiAnalysisModel.processing_stage == "cancelled"
        if status == "retrying":
            return (
                (AssetAiAnalysisModel.status == "pending")
                & (AssetAiAnalysisModel.processing_stage == "retry")
            )
        return AssetAiAnalysisModel.status == status

    def _analysis_conditions(self, f: AiOperationsFilters, *, timestamp_column=None):
        timestamp = timestamp_column if timestamp_column is not None else AssetAiAnalysisModel.created_at
        conditions = [
            AssetAiAnalysisModel.tenant_id == f.tenant_id,
            timestamp >= f.from_at,
            timestamp < f.to_at,
        ]
        if f.provider:
            conditions.append(AssetAiAnalysisModel.ai_provider == f.provider)
        if f.model:
            conditions.append(AssetAiAnalysisModel.ai_model == f.model)
        if f.processing_mode == "batch":
            conditions.append(AssetAiAnalysisModel.pipeline_version.like("batch%"))
        elif f.processing_mode == "single":
            conditions.append(~AssetAiAnalysisModel.pipeline_version.like("batch%"))
        if f.metadata_profile:
            conditions.append(AssetAiAnalysisModel.metadata_profile == f.metadata_profile)
        status = self._analysis_status(f.status)
        if status is not None:
            conditions.append(status)
        source = self._source_exists(AssetAiAnalysisModel.asset_id, f)
        if source is not None:
            conditions.append(source)
        return conditions

    def _usage_conditions(self, f: AiOperationsFilters):
        conditions = [
            AiUsageRecordModel.tenant_id == f.tenant_id,
            AiUsageRecordModel.occurred_at >= f.from_at,
            AiUsageRecordModel.occurred_at < f.to_at,
        ]
        if f.provider:
            conditions.append(AiUsageRecordModel.provider == f.provider)
        if f.model:
            conditions.append(AiUsageRecordModel.model == f.model)
        if f.processing_mode:
            conditions.append(AiUsageRecordModel.processing_mode == f.processing_mode)
        if f.metadata_profile:
            conditions.append(AiUsageRecordModel.metadata_profile == f.metadata_profile)
        if f.status:
            conditions.append(self._analysis_status(f.status))
        source = self._source_exists(AiUsageRecordModel.asset_id, f)
        if source is not None:
            conditions.append(source)
        return conditions

    def _usage_select(self, *columns, filters: AiOperationsFilters):
        statement = select(*columns)
        if filters.status:
            statement = statement.join(
                AssetAiAnalysisModel,
                (AssetAiAnalysisModel.id == AiUsageRecordModel.analysis_id)
                & (AssetAiAnalysisModel.tenant_id == AiUsageRecordModel.tenant_id),
            )
        return statement.where(*self._usage_conditions(filters))

    def _latency(self, f: AiOperationsFilters) -> dict[str, float]:
        if self.dialect == "postgresql":
            row = self.session.execute(self._usage_select(
                func.coalesce(func.avg(AiUsageRecordModel.latency_ms), 0),
                func.coalesce(func.percentile_cont(0.5).within_group(AiUsageRecordModel.latency_ms), 0),
                func.coalesce(func.percentile_cont(0.95).within_group(AiUsageRecordModel.latency_ms), 0),
                filters=f,
            )).one()
            return {"average_ms": float(row[0]), "p50_ms": float(row[1]), "p95_ms": float(row[2])}

        # SQLite has no percentile_cont. This is used only by local/unit tests;
        # production PostgreSQL performs all percentile work in the database.
        values = sorted(self.session.scalars(
            self._usage_select(AiUsageRecordModel.latency_ms, filters=f)
        ).all())
        if not values:
            return {"average_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}

        def percentile(q: float) -> float:
            index = max(0, min(len(values) - 1, round((len(values) - 1) * q)))
            return float(values[index])

        return {
            "average_ms": float(sum(values) / len(values)),
            "p50_ms": percentile(0.5),
            "p95_ms": percentile(0.95),
        }

    def _reservation_conditions(self, f: AiOperationsFilters):
        conditions = [
            AiBudgetReservationModel.tenant_id == f.tenant_id,
            AiBudgetReservationModel.status == "reconciled",
            AiBudgetReservationModel.updated_at >= f.from_at,
            AiBudgetReservationModel.updated_at < f.to_at,
        ]
        if f.provider:
            conditions.append(AiBudgetReservationModel.provider == f.provider)
        if f.model:
            conditions.append(AiBudgetReservationModel.model == f.model)
        if f.processing_mode:
            conditions.append(AiBudgetReservationModel.processing_mode == f.processing_mode)
        if f.metadata_profile:
            conditions.append(AssetAiAnalysisModel.metadata_profile == f.metadata_profile)
        if f.status:
            conditions.append(self._analysis_status(f.status))
        source = self._source_exists(AssetAiAnalysisModel.asset_id, f)
        if source is not None:
            conditions.append(source)
        return conditions

    def _reservation_select(self, *columns, filters: AiOperationsFilters):
        statement = select(*columns)
        if filters.metadata_profile or filters.status or filters.source_provider:
            statement = statement.join(
                AssetAiAnalysisModel,
                (AssetAiAnalysisModel.id == AiBudgetReservationModel.analysis_id)
                & (AssetAiAnalysisModel.tenant_id == AiBudgetReservationModel.tenant_id),
            )
        return statement.where(*self._reservation_conditions(filters))

    def _reconciled_cost(self, f: AiOperationsFilters) -> int:
        return int(self.session.scalar(self._reservation_select(
            func.coalesce(func.sum(AiBudgetReservationModel.actual_cost_micros), 0),
            filters=f,
        )) or 0)

    def summary(self, f: AiOperationsFilters) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        deferred_job = (
            (ProcessingJobModel.status == "pending")
            & ProcessingJobModel.last_error_code.in_(DEFERRED_AI_REASON_CODES)
            & ProcessingJobModel.next_attempt_at.is_not(None)
            & (ProcessingJobModel.next_attempt_at > now)
        )
        deferred_for_analysis = exists(select(literal(1)).where(
            ProcessingJobModel.tenant_id == AssetAiAnalysisModel.tenant_id,
            ProcessingJobModel.entity_id == AssetAiAnalysisModel.id,
            ProcessingJobModel.job_type == "asset_analyze",
            deferred_job,
        ))
        queued = case((
            (AssetAiAnalysisModel.status == "pending")
            & (func.coalesce(AssetAiAnalysisModel.processing_stage, "") != "cancelled")
            & ~deferred_for_analysis, 1
        ), else_=0)
        status_count = lambda value: func.sum(case((AssetAiAnalysisModel.status == value, 1), else_=0))
        row = self.session.execute(select(
            func.count(AssetAiAnalysisModel.id), func.coalesce(func.sum(queued), 0),
            func.coalesce(status_count("running"), 0), func.coalesce(status_count("completed"), 0),
            func.coalesce(status_count("failed"), 0),
            func.coalesce(func.sum(case((AssetAiAnalysisModel.processing_stage == "cancelled", 1), else_=0)), 0),
            func.coalesce(status_count("budget_blocked"), 0),
        ).where(*self._analysis_conditions(f))).one()
        requested, queued_count, running, completed, failed, cancelled, blocked = map(int, row)
        deferred_conditions = [
            ProcessingJobModel.tenant_id == f.tenant_id,
            ProcessingJobModel.job_type.in_(AI_JOB_TYPES),
            ProcessingJobModel.created_at >= f.from_at,
            ProcessingJobModel.created_at < f.to_at,
            deferred_job,
        ]
        if f.provider:
            deferred_conditions.append(ProcessingJobModel.provider_key == f.provider)
        deferred, next_deferred_retry_at = self.session.execute(select(
            func.count(ProcessingJobModel.id), func.min(ProcessingJobModel.next_attempt_at),
        ).where(*deferred_conditions)).one()
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
            "completed": completed, "failed": failed, "cancelled": cancelled,
            "budget_blocked": blocked, "deferred": int(deferred or 0),
            "next_deferred_retry_at": next_deferred_retry_at,
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

    def _day(self, column):
        if self.dialect == "postgresql":
            return func.date_trunc("day", func.timezone("UTC", column))
        return func.date(column)

    @staticmethod
    def _day_key(value) -> str:
        return value.isoformat()[:10] if isinstance(value, date) else str(value)[:10]

    def daily(self, f: AiOperationsFilters) -> list[dict[str, Any]]:
        analysis_day = self._day(AssetAiAnalysisModel.created_at).label("day")
        analyses = self.session.execute(select(
            analysis_day, func.count(AssetAiAnalysisModel.id),
        ).where(*self._analysis_conditions(f)).group_by(analysis_day).order_by(analysis_day)).all()
        terminal_day = self._day(AssetAiAnalysisModel.completed_at).label("day")
        terminals = self.session.execute(select(
            terminal_day,
            func.coalesce(func.sum(case((AssetAiAnalysisModel.status == "completed", 1), else_=0)), 0),
            func.coalesce(func.sum(case((AssetAiAnalysisModel.status == "failed", 1), else_=0)), 0),
        ).where(
            *self._analysis_conditions(f, timestamp_column=AssetAiAnalysisModel.completed_at),
            AssetAiAnalysisModel.status.in_(("completed", "failed")),
        ).group_by(terminal_day).order_by(terminal_day)).all()
        usage_day = self._day(AiUsageRecordModel.occurred_at).label("day")
        usage = self.session.execute(self._usage_select(
            usage_day,
            AiUsageRecordModel.provider,
            func.coalesce(func.sum(AiUsageRecordModel.locally_estimated_cost_micros), 0),
            func.coalesce(func.sum(AiUsageRecordModel.provider_reported_cost_micros), 0),
            func.coalesce(func.sum(AiUsageRecordModel.latency_ms), 0),
            func.count(AiUsageRecordModel.id),
            filters=f,
        ).group_by(usage_day, AiUsageRecordModel.provider).order_by(
            usage_day, AiUsageRecordModel.provider,
        )).all()
        reservation_day = self._day(AiBudgetReservationModel.updated_at).label("day")
        reservations = self.session.execute(self._reservation_select(
            reservation_day,
            func.coalesce(func.sum(AiBudgetReservationModel.actual_cost_micros), 0),
            filters=f,
        ).group_by(reservation_day).order_by(reservation_day)).all()
        values: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "requested": 0, "completed": 0, "failed": 0,
            "estimated_cost_micros": 0, "provider_reported_cost_micros": 0,
            "reconciled_cost_micros": 0, "average_latency_ms": 0.0,
            "provider_estimated_cost_micros": {},
            "_latency_total": 0, "_latency_count": 0,
        })
        for day, requested in analyses:
            values[self._day_key(day)]["requested"] = int(requested)
        for day, completed, failed in terminals:
            values[self._day_key(day)].update(completed=int(completed), failed=int(failed))
        for day, provider, estimated, reported, latency_total, latency_count in usage:
            value = values[self._day_key(day)]
            value["estimated_cost_micros"] += int(estimated)
            value["provider_reported_cost_micros"] += int(reported)
            value["provider_estimated_cost_micros"][provider] = int(estimated)
            value["_latency_total"] += int(latency_total)
            value["_latency_count"] += int(latency_count)
        for day, reconciled in reservations:
            values[self._day_key(day)]["reconciled_cost_micros"] = int(reconciled)
        result = []
        for day in sorted(values):
            value = values[day]
            count = value.pop("_latency_count")
            latency_total = value.pop("_latency_total")
            value["average_latency_ms"] = latency_total / count if count else 0.0
            result.append({"date": day, **value})
        return result
