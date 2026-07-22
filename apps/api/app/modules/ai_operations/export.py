from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ai_operations.queries import AiOperationsRepository
from app.modules.ai_operations.schema import AiOperationsFilters
from app.modules.processing_policy.repository import ProcessingPolicyRepository


EXPORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "daily": (
        "date", "requested", "completed", "failed",
        "estimated_cost_micros", "provider_reported_cost_micros",
        "reconciled_cost_micros", "average_latency_ms",
    ),
    "usage": (
        "id", "asset_id", "analysis_id", "job_id", "occurred_at",
        "provider", "model", "processing_mode", "metadata_profile",
        "input_units", "output_units", "media_units",
        "estimated_cost_micros", "provider_reported_cost_micros",
        "currency", "latency_ms", "outcome", "retry_count",
    ),
    "failures": ("source", "error_code", "count"),
    "jobs": (
        "id", "job_type", "entity_type", "entity_id", "provider", "status",
        "attempt_count", "max_attempts", "next_attempt_at", "created_at",
        "completed_at", "error_code", "retryable",
    ),
}


def audit_export(
    session: Session,
    *,
    actor_id: str,
    filters: AiOperationsFilters,
    export_type: str,
    row_limit: int,
) -> None:
    ProcessingPolicyRepository(session).audit(
        actor_id=actor_id,
        tenant_id=filters.tenant_id,
        action="ai_operations_export_requested",
        old_policy={},
        new_policy={
            "export_type": export_type,
            "from": filters.from_at.isoformat(),
            "to": filters.to_at.isoformat(),
            "provider": filters.provider,
            "model": filters.model,
            "processing_mode": filters.processing_mode,
            "metadata_profile": filters.metadata_profile,
            "status": filters.status,
            "source_provider": filters.source_provider,
            "row_limit": row_limit,
        },
        reason="Tenant administrator requested a bounded AI Operations CSV export.",
    )


def export_rows(
    repository: AiOperationsRepository,
    *,
    export_type: str,
    filters: AiOperationsFilters,
    row_limit: int,
) -> Iterable[dict[str, Any]]:
    if export_type == "daily":
        return repository.daily(filters)[:row_limit]
    if export_type == "failures":
        return repository.failures(filters)[:row_limit]
    if export_type == "usage":
        return repository.iter_usage(filters, row_limit=row_limit)
    if export_type == "jobs":
        return repository.iter_jobs(filters, row_limit=row_limit)
    raise ValueError("unsupported AI Operations export type")


def csv_stream(columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> Iterator[str]:
    yield _csv_line(columns)
    for row in rows:
        yield _csv_line(tuple(_safe_cell(row.get(column)) for column in columns))


def _csv_line(values: tuple[Any, ...]) -> str:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerow(values)
    return output.getvalue()


def _safe_cell(value: Any) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    # Prevent spreadsheet formula execution when an identifier ever starts with
    # a formula marker. CSV remains data, never executable content.
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text