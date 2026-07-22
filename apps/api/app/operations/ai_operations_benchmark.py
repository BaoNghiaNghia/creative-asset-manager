from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.ai_governance.model import AiUsageRecordModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_operations.queries import AiOperationsRepository
from app.modules.ai_operations.schema import AiOperationsFilters
from app.modules.assets.model import AssetModel


def _seed(session: Session, tenant_id: str, rows: int, now: datetime) -> None:
    asset_id, profile_id = str(uuid4()), str(uuid4())
    session.add(AssetModel(id=asset_id, tenant_id=tenant_id, content_hash=uuid4().hex * 2))
    session.add(MetadataProfileModel(
        id=profile_id, tenant_id=tenant_id, profile_name="benchmark",
        profile_version="1", prompt_template="benchmark", search_config_json={},
        active=True,
    ))
    session.flush()
    chunk = 2_000
    for start in range(0, rows, chunk):
        analyses, usages = [], []
        for index in range(start, min(start + chunk, rows)):
            analysis_id = f"bench-{index:030d}"
            occurred_at = now - timedelta(days=index % 90, seconds=index % 86_400)
            provider = "gemini" if index % 2 == 0 else "openai"
            mode = "single" if index % 3 else "batch"
            outcome = "completed" if index % 10 else "provider_failed"
            status = "completed" if outcome == "completed" else "failed"
            analyses.append({
                "id": analysis_id, "tenant_id": tenant_id, "asset_id": asset_id,
                "content_hash": "f" * 64, "metadata_profile_id": profile_id,
                "metadata_profile": "benchmark", "metadata_profile_version": "1",
                "prompt_version": f"p-{index}", "pipeline_version": f"{mode}-asset-v1",
                "ai_provider": provider, "ai_model": f"{provider}-benchmark",
                "status": status, "processing_stage": status,
                "forced": False, "attempt_count": index % 3,
                "created_at": occurred_at - timedelta(minutes=2),
                "updated_at": occurred_at, "completed_at": occurred_at,
                "last_error_code": "provider_timeout" if status == "failed" else None,
            })
            usages.append({
                "id": str(uuid4()), "tenant_id": tenant_id,
                "provider_operation_key": f"benchmark:{index}",
                "asset_id": asset_id, "analysis_id": analysis_id,
                "provider": provider, "processing_mode": mode,
                "model": f"{provider}-benchmark", "metadata_profile": "benchmark",
                "metadata_profile_version": "1", "input_units": 100,
                "output_units": 20, "media_units": 1,
                "locally_estimated_cost_micros": 25,
                "provider_reported_cost_micros": 20 if outcome == "completed" else None,
                "currency": "USD", "latency_ms": 100 + index % 900,
                "outcome": outcome, "retry_count": index % 3,
                "occurred_at": occurred_at,
            })
        session.bulk_insert_mappings(AssetAiAnalysisModel, analyses)
        session.bulk_insert_mappings(AiUsageRecordModel, usages)
        session.commit()


def _cleanup(session: Session, tenant_id: str) -> None:
    session.execute(delete(AiUsageRecordModel).where(AiUsageRecordModel.tenant_id == tenant_id))
    session.execute(delete(AssetAiAnalysisModel).where(AssetAiAnalysisModel.tenant_id == tenant_id))
    session.execute(delete(MetadataProfileModel).where(MetadataProfileModel.tenant_id == tenant_id))
    session.execute(delete(AssetModel).where(AssetModel.tenant_id == tenant_id))
    session.commit()


def benchmark(database_url: str, rows: int, repeats: int, threshold_ms: float, create_schema: bool) -> dict:
    engine = create_engine(database_url, pool_pre_ping=True)
    if create_schema:
        Base.metadata.create_all(engine)
    tenant_id = f"ai-ops-benchmark-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    try:
        with Session(engine) as session:
            _seed(session, tenant_id, rows, now)
            filters = AiOperationsFilters(
                tenant_id=tenant_id, from_at=now - timedelta(days=90),
                to_at=now + timedelta(seconds=1),
            )
            repository = AiOperationsRepository(session)
            operations = {
                "summary": repository.summary,
                "daily": repository.daily,
                "providers": repository.providers,
                "failures": repository.failures,
            }
            timings: dict[str, list[float]] = {name: [] for name in operations}
            for operation in operations.values():
                operation(filters)
            for _ in range(repeats):
                for name, operation in operations.items():
                    started = time.perf_counter()
                    operation(filters)
                    timings[name].append((time.perf_counter() - started) * 1000)
            if engine.dialect.name == "postgresql":
                plan_sql = "EXPLAIN (FORMAT JSON) SELECT count(*) FROM ai_usage_records WHERE tenant_id=:tenant AND occurred_at>=:start AND occurred_at<:end"
            else:
                plan_sql = "EXPLAIN QUERY PLAN SELECT count(*) FROM ai_usage_records WHERE tenant_id=:tenant AND occurred_at>=:start AND occurred_at<:end"
            plan = [" | ".join(str(value) for value in row) for row in session.execute(text(plan_sql), {
                "tenant": tenant_id, "start": filters.from_at, "end": filters.to_at,
            })]
            result = {
                "dialect": engine.dialect.name,
                "rows": rows,
                "range_days": 90,
                "threshold_ms": threshold_ms,
                "timings_ms": {
                    name: {"min": round(min(values), 2), "average": round(sum(values) / len(values), 2), "max": round(max(values), 2)}
                    for name, values in timings.items()
                },
                "within_threshold": all(max(values) <= threshold_ms for values in timings.values()),
                "representative_plan": plan,
            }
            return result
    finally:
        with Session(engine) as session:
            _cleanup(session, tenant_id)
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark bounded AI Operations aggregation queries.")
    parser.add_argument("--database-url", help="Migrated PostgreSQL URL. Omit for an isolated temporary SQLite diagnostic.")
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--threshold-ms", type=float, default=750.0)
    parser.add_argument("--allow-write", action="store_true", help="Required when benchmarking a supplied database.")
    args = parser.parse_args()
    if args.rows < 1 or args.repeats < 1:
        parser.error("rows and repeats must be positive")
    if args.database_url and not args.allow_write:
        parser.error("--allow-write is required for a supplied database")
    if args.database_url:
        result = benchmark(args.database_url, args.rows, args.repeats, args.threshold_ms, False)
    else:
        with tempfile.TemporaryDirectory(prefix="cam-ai-ops-") as directory:
            url = f"sqlite:///{Path(directory) / 'benchmark.db'}"
            result = benchmark(url, args.rows, args.repeats, args.threshold_ms, True)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["within_threshold"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
