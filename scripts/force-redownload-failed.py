from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.ai_operations.pipeline import (
    PipelineOperationsRepository,
    SUPPORTED_IMAGE_MIME_TYPES,
)
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.service import AssetPipelineService
from app.modules.pipeline.state import PipelineState
from app.modules.processing.repository import ProcessingRepository


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--apply", action="store_true")
    value.add_argument("--limit", type=int, default=5000)
    return value


def reset_download_job(job) -> None:
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
    job.last_error_code = "operator_force_redownload"
    job.last_error_message = "Operator requested a fresh source download."
    job.updated_at = now


def main() -> int:
    args = parser().parse_args()
    if args.limit < 1 or args.limit > 20000:
        raise SystemExit("--limit must be between 1 and 20000")

    with SessionLocal() as session:
        tenant_ids = sorted(
            str(value)
            for value in set(session.scalars(select(SourceAssetModel.tenant_id).distinct()))
            if value
        )
        total_selected = 0
        total_requeued = 0

        for tenant_id in tenant_ids:
            ops = PipelineOperationsRepository(session)
            active_source_ids, _ = ops._active_source_ids(tenant_id)
            if not active_source_ids:
                continue

            rows = list(
                session.execute(
                    select(
                        AssetPipelineModel,
                        SourceAssetModel,
                        ExternalSourceModel.source_type,
                    )
                    .join(
                        SourceAssetModel,
                        SourceAssetModel.id == AssetPipelineModel.source_asset_id,
                    )
                    .join(
                        ExternalSourceModel,
                        ExternalSourceModel.id == SourceAssetModel.external_source_id,
                    )
                    .where(
                        AssetPipelineModel.tenant_id == tenant_id,
                        AssetPipelineModel.state == PipelineState.DOWNLOAD_FAILED.value,
                        SourceAssetModel.tenant_id == tenant_id,
                        SourceAssetModel.deleted_at.is_(None),
                        SourceAssetModel.external_source_id.in_(active_source_ids),
                        SourceAssetModel.mime_type.in_(SUPPORTED_IMAGE_MIME_TYPES),
                    )
                    .order_by(AssetPipelineModel.updated_at, AssetPipelineModel.id)
                    .limit(max(0, args.limit - total_selected))
                    .with_for_update()
                )
            )
            if not rows:
                continue

            total_selected += len(rows)
            errors = Counter(
                pipeline.last_error_code or "unknown"
                for pipeline, _source, _source_type in rows
            )
            sources = Counter(
                source_type or "unknown"
                for _pipeline, _source, source_type in rows
            )
            print(
                "FORCE_REDOWNLOAD "
                f"mode={'apply' if args.apply else 'preview'} "
                f"tenant=[redacted] selected={len(rows)} "
                f"errors={dict(errors)} sources={dict(sources)}"
            )

            if not args.apply:
                continue

            pipelines = AssetPipelineRepository(session)
            jobs = ProcessingRepository(session)
            coordinator = AssetPipelineService(pipelines, jobs)

            for pipeline, source_asset, _source_type in rows:
                pipelines.transition(pipeline, PipelineState.DOWNLOAD_PENDING)
                coordinator.enqueue(
                    pipeline,
                    "source_asset_download",
                    entity_type="source_asset",
                    entity_id=source_asset.id,
                    transition=False,
                )
                identity = pipeline.source_asset_id or pipeline.origin_id
                key = f"pipeline:{pipeline.id}:source_asset_download:{identity}"
                job = jobs.get_job_by_key(tenant_id, key)
                if job is None:
                    raise RuntimeError(f"download job missing after enqueue: {pipeline.id}")
                reset_download_job(job)
                total_requeued += 1

            session.commit()
            if total_selected >= args.limit:
                break

        print(
            "FORCE_REDOWNLOAD "
            f"mode={'apply' if args.apply else 'preview'} "
            f"selected={total_selected} requeued={total_requeued}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
