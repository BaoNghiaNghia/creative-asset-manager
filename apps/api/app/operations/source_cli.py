from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.source_sync.service import initial_source_asset_download_key
from app.providers.google.incremental import normalize_drive_file_mime_type


def _canonical_source(sources: list[ExternalSourceModel], counts: dict[str, int]) -> ExternalSourceModel:
    return max(
        sources,
        key=lambda source: (
            counts.get(source.id, 0),
            bool((source.source_metadata or {}).get("is_default")),
            source.updated_at,
            source.id,
        ),
    )


def repair_google_drive_duplicates(
    *,
    tenant_id: str,
    apply: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, object]:
    if not tenant_id:
        raise ValueError("tenant_id is required")

    with session_factory() as session:
        sources = list(
            session.scalars(
                select(ExternalSourceModel).where(
                    ExternalSourceModel.tenant_id == tenant_id,
                    ExternalSourceModel.source_type == "google_drive",
                )
            )
        )
        by_account: dict[str, list[ExternalSourceModel]] = defaultdict(list)
        for source in sources:
            account_id = str((source.source_metadata or {}).get("provider_account_id") or "")
            if account_id:
                by_account[account_id].append(source)

        duplicate_groups = {
            account_id: group
            for account_id, group in by_account.items()
            if len(group) > 1
        }
        all_ids = [source.id for group in duplicate_groups.values() for source in group]
        counts = dict(
            session.execute(
                select(SourceAssetModel.external_source_id, func.count(SourceAssetModel.id))
                .where(
                    SourceAssetModel.tenant_id == tenant_id,
                    SourceAssetModel.external_source_id.in_(all_ids or [""]),
                    SourceAssetModel.deleted_at.is_(None),
                )
                .group_by(SourceAssetModel.external_source_id)
            ).all()
        )

        groups: list[dict[str, object]] = []
        duplicate_source_ids: list[str] = []
        for account_id, group in sorted(duplicate_groups.items()):
            canonical = _canonical_source(group, counts)
            duplicates = [source for source in group if source.id != canonical.id]
            duplicate_source_ids.extend(source.id for source in duplicates)
            groups.append(
                {
                    "provider_account_id": account_id,
                    "canonical_source_id": canonical.id,
                    "canonical_source_key": canonical.source_key,
                    "duplicate_source_ids": [source.id for source in duplicates],
                    "canonical_source_assets": int(counts.get(canonical.id, 0)),
                    "duplicate_source_assets": sum(
                        int(counts.get(source.id, 0)) for source in duplicates
                    ),
                }
            )

        source_asset_ids = list(
            session.scalars(
                select(SourceAssetModel.id).where(
                    SourceAssetModel.tenant_id == tenant_id,
                    SourceAssetModel.external_source_id.in_(duplicate_source_ids or [""]),
                    SourceAssetModel.deleted_at.is_(None),
                )
            )
        )
        unstarted = (
            (ProcessingJobModel.entity_type == "external_source")
            & (ProcessingJobModel.entity_id.in_(duplicate_source_ids or [""]))
        ) | (
            (ProcessingJobModel.entity_type == "source_asset")
            & (ProcessingJobModel.entity_id.in_(source_asset_ids or [""]))
        )
        unstarted = (
            ProcessingJobModel.tenant_id == tenant_id
        ) & unstarted & ProcessingJobModel.status.in_(("pending", "retry"))
        active = (
            (ProcessingJobModel.entity_type == "external_source")
            & (ProcessingJobModel.entity_id.in_(duplicate_source_ids or [""]))
        ) | (
            (ProcessingJobModel.entity_type == "source_asset")
            & (ProcessingJobModel.entity_id.in_(source_asset_ids or [""]))
        )
        active = (
            ProcessingJobModel.tenant_id == tenant_id
        ) & active & (ProcessingJobModel.status == "processing")
        pending_jobs = int(
            session.scalar(select(func.count()).select_from(ProcessingJobModel).where(unstarted))
            or 0
        )
        processing_jobs = int(
            session.scalar(select(func.count()).select_from(ProcessingJobModel).where(active))
            or 0
        )

        if apply and duplicate_source_ids:
            now = datetime.now(timezone.utc)
            for group in groups:
                canonical_id = str(group["canonical_source_id"])
                canonical = session.get(ExternalSourceModel, canonical_id)
                canonical_metadata = dict(canonical.source_metadata or {})
                canonical_metadata["is_default"] = True
                canonical.source_metadata = canonical_metadata
                for duplicate_id in group["duplicate_source_ids"]:
                    source = session.get(ExternalSourceModel, duplicate_id)
                    metadata = dict(source.source_metadata or {})
                    metadata.update(
                        {
                            "is_default": False,
                            "decommissioned_at": now.isoformat(),
                            "decommissioned_reason": "duplicate_google_drive_source",
                            "canonical_source_id": canonical_id,
                        }
                    )
                    source.source_metadata = metadata
            session.execute(
                update(SourceAssetModel)
                .where(
                    SourceAssetModel.tenant_id == tenant_id,
                    SourceAssetModel.id.in_(source_asset_ids or [""]),
                    SourceAssetModel.deleted_at.is_(None),
                )
                .values(deleted_at=now, updated_at=now)
            )
            session.execute(delete(ProcessingJobModel).where(unstarted))
            session.execute(
                update(ProcessingJobModel)
                .where(active)
                .values(
                    cancellation_requested=True,
                    cancel_requested_at=now,
                    cancel_requested_by="source-repair",
                    cancellation_reason="duplicate_google_drive_source",
                    updated_at=now,
                )
            )
            session.commit()
        else:
            session.rollback()

    return {
        "tenant_id": tenant_id,
        "dry_run": not apply,
        "duplicate_account_groups": len(groups),
        "duplicate_sources": len(duplicate_source_ids),
        "duplicate_source_assets": len(source_asset_ids),
        "unstarted_jobs": pending_jobs,
        "processing_jobs_cancel_requested": processing_jobs if apply else 0,
        "unstarted_jobs_removed": pending_jobs if apply else 0,
        "groups": groups,
    }


def backfill_google_drive_modern_images(
    *,
    tenant_id: str,
    apply: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, object]:
    """Normalize and enqueue previously skipped AVIF/HEIC/HEIF source assets."""
    if not tenant_id:
        raise ValueError("tenant_id is required")

    with session_factory() as session:
        modern_name = or_(
            func.lower(SourceAssetModel.filename).like("%.avif"),
            func.lower(SourceAssetModel.filename).like("%.heic"),
            func.lower(SourceAssetModel.filename).like("%.heif"),
        )
        assets = list(session.scalars(
            select(SourceAssetModel)
            .join(
                ExternalSourceModel,
                (ExternalSourceModel.tenant_id == SourceAssetModel.tenant_id)
                & (ExternalSourceModel.id == SourceAssetModel.external_source_id),
            )
            .where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.deleted_at.is_(None),
                ExternalSourceModel.source_type == "google_drive",
                modern_name,
            )
            .order_by(SourceAssetModel.id)
        ))
        asset_ids = [asset.id for asset in assets]
        pipelines = {
            pipeline.origin_id: pipeline
            for pipeline in session.scalars(
                select(AssetPipelineModel).where(
                    AssetPipelineModel.tenant_id == tenant_id,
                    AssetPipelineModel.origin_type == "source_asset",
                    AssetPipelineModel.origin_id.in_(asset_ids or [""]),
                )
            )
        }
        keys = {asset.id: initial_source_asset_download_key(asset.id) for asset in assets}
        existing_keys = set(session.scalars(
            select(ProcessingJobModel.idempotency_key).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.idempotency_key.in_(list(keys.values()) or [""]),
            )
        ))
        would_normalize = 0
        eligible = 0
        already_imported = 0
        would_enqueue = 0
        processing = ProcessingRepository(session)
        jobs_before = processing.count_jobs()

        for asset in assets:
            canonical_mime = normalize_drive_file_mime_type(asset.filename, asset.mime_type)
            if canonical_mime != asset.mime_type:
                would_normalize += 1
                if apply:
                    asset.mime_type = canonical_mime

            pipeline = pipelines.get(asset.id)
            imported = pipeline is not None and pipeline.asset_id is not None
            retryable_unimported = (
                pipeline is None
                or (
                    pipeline.asset_id is None
                    and pipeline.state in {"discovered", "download_failed"}
                )
            )
            if imported:
                already_imported += 1
            if not retryable_unimported:
                continue
            eligible += 1
            key = keys[asset.id]
            if key in existing_keys:
                continue
            would_enqueue += 1
            if apply:
                processing.create_job(
                    tenant_id=tenant_id,
                    job_type="source_asset_download",
                    entity_type="source_asset",
                    entity_id=asset.id,
                    idempotency_key=key,
                    payload={"source_asset_id": asset.id},
                    provider_key="google_drive",
                    provider_scope="source",
                )

        if apply:
            session.commit()
        else:
            session.rollback()
        jobs_created = processing.count_jobs() - jobs_before if apply else 0

    return {
        "tenant_id": tenant_id,
        "dry_run": not apply,
        "matched": len(assets),
        "mime_normalized": would_normalize if apply else 0,
        "would_normalize_mime": would_normalize,
        "eligible_unimported": eligible,
        "already_imported": already_imported,
        "would_enqueue": would_enqueue,
        "jobs_created": jobs_created,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Creative Asset Manager source operations")
    value.add_argument("command", choices=("google-drive:repair-duplicates", "google-drive:backfill-modern-images"))
    value.add_argument("--tenant-id", required=True)
    value.add_argument("--apply", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    if args.command == "google-drive:backfill-modern-images":
        result = backfill_google_drive_modern_images(
            tenant_id=args.tenant_id,
            apply=args.apply,
        )
    else:
        result = repair_google_drive_duplicates(
            tenant_id=args.tenant_id,
            apply=args.apply,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
