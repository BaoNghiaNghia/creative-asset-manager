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
from app.modules.processing.model import ProcessingJobModel


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


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Creative Asset Manager source operations")
    value.add_argument("command", choices=("google-drive:repair-duplicates",))
    value.add_argument("--tenant-id", required=True)
    value.add_argument("--apply", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    result = repair_google_drive_duplicates(
        tenant_id=args.tenant_id,
        apply=args.apply,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
