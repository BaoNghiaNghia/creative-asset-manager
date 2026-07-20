from __future__ import annotations

from collections import defaultdict
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import (
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.search.operations_model import (
    SearchOperationItemModel,
    SearchOperationRunModel,
)
from app.modules.storage.model import AssetStorageObjectModel

AssetProcessingStatus = Literal[
    "discovered",
    "stored",
    "analyzing",
    "metadata_ready",
    "indexed",
    "duplicate",
    "failed",
]

_SOURCE_TYPES = {
    "google-drive": "google_drive",
    "sharepoint": "sharepoint",
}
_ACTIVE_JOB_STATUSES = {"pending", "processing", "retry"}
_RELEVANT_ASSET_JOBS = {
    "asset_store",
    "asset_analyze",
    "search_projection_build",
    "asset_index",
}


class AssetProcessingStatusService:
    """Build read-only UI status projections from authoritative PostgreSQL state."""

    def __init__(self, session: Session):
        self.session = session

    def list(
        self,
        tenant_id: str,
        provider: str,
        item_ids: list[str],
    ) -> dict[str, AssetProcessingStatus]:
        unique_ids = tuple(dict.fromkeys(item_ids))
        statuses: dict[str, AssetProcessingStatus] = {
            item_id: "discovered" for item_id in unique_ids
        }
        if not unique_ids:
            return statuses

        source_type = _SOURCE_TYPES.get(provider)
        if source_type is None:
            return statuses

        source_rows = self.session.execute(
            select(
                SourceAssetModel.external_asset_id,
                SourceAssetModel.id,
                AssetSourceLinkModel.asset_id,
            )
            .join(
                ExternalSourceModel,
                ExternalSourceModel.id == SourceAssetModel.external_source_id,
            )
            .outerjoin(
                AssetSourceLinkModel,
                AssetSourceLinkModel.source_asset_id == SourceAssetModel.id,
            )
            .where(
                SourceAssetModel.tenant_id == tenant_id,
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.source_type == source_type,
                SourceAssetModel.external_asset_id.in_(unique_ids),
                SourceAssetModel.deleted_at.is_(None),
            )
        ).all()
        if not source_rows:
            return statuses

        source_ids_by_item: dict[str, set[str]] = defaultdict(set)
        asset_ids_by_item: dict[str, set[str]] = defaultdict(set)
        for item_id, source_asset_id, asset_id in source_rows:
            source_ids_by_item[item_id].add(source_asset_id)
            if asset_id:
                asset_ids_by_item[item_id].add(asset_id)

        asset_ids = tuple(
            sorted({asset_id for values in asset_ids_by_item.values() for asset_id in values})
        )
        source_asset_ids = tuple(
            sorted({source_id for values in source_ids_by_item.values() for source_id in values})
        )

        duplicate_assets: set[str] = set()
        stored_assets: set[str] = set()
        storage_failed_assets: set[str] = set()
        indexed_assets: set[str] = set()
        latest_analysis: dict[str, AssetAiAnalysisModel] = {}
        latest_jobs: dict[tuple[str, str], ProcessingJobModel] = {}

        if asset_ids:
            duplicate_assets = {
                asset_id
                for asset_id, count in self.session.execute(
                    select(AssetSourceLinkModel.asset_id, func.count())
                    .where(
                        AssetSourceLinkModel.tenant_id == tenant_id,
                        AssetSourceLinkModel.asset_id.in_(asset_ids),
                    )
                    .group_by(AssetSourceLinkModel.asset_id)
                    .having(func.count() > 1)
                )
            }

            storage_rows = list(
                self.session.scalars(
                    select(AssetStorageObjectModel)
                    .where(
                        AssetStorageObjectModel.tenant_id == tenant_id,
                        AssetStorageObjectModel.asset_id.in_(asset_ids),
                    )
                    .order_by(
                        AssetStorageObjectModel.asset_id,
                        AssetStorageObjectModel.updated_at.desc(),
                    )
                )
            )
            storage_by_asset: dict[str, list[str]] = defaultdict(list)
            for storage in storage_rows:
                storage_by_asset[storage.asset_id].append(storage.status)
            stored_assets = {
                asset_id
                for asset_id, values in storage_by_asset.items()
                if "stored" in values
            }
            storage_failed_assets = {
                asset_id
                for asset_id, values in storage_by_asset.items()
                if "stored" not in values and values and values[0] == "failed"
            }

            for analysis in self.session.scalars(
                select(AssetAiAnalysisModel)
                .where(
                    AssetAiAnalysisModel.tenant_id == tenant_id,
                    AssetAiAnalysisModel.asset_id.in_(asset_ids),
                )
                .order_by(
                    AssetAiAnalysisModel.asset_id,
                    AssetAiAnalysisModel.created_at.desc(),
                    AssetAiAnalysisModel.id.desc(),
                )
            ):
                latest_analysis.setdefault(analysis.asset_id, analysis)

            indexed_assets = set(
                self.session.scalars(
                    select(SearchOperationItemModel.asset_id)
                    .join(
                        SearchOperationRunModel,
                        SearchOperationRunModel.id == SearchOperationItemModel.run_id,
                    )
                    .where(
                        SearchOperationItemModel.tenant_id == tenant_id,
                        SearchOperationItemModel.asset_id.in_(asset_ids),
                        SearchOperationItemModel.status == "completed",
                        SearchOperationRunModel.tenant_id == tenant_id,
                        SearchOperationRunModel.status == "completed",
                        SearchOperationRunModel.operation_type.in_(
                            ("reindex_assets", "rebuild_and_reindex")
                        ),
                    )
                    .distinct()
                )
            )

        job_entity_ids = tuple(sorted({*asset_ids, *source_asset_ids}))
        if job_entity_ids:
            for job in self.session.scalars(
                select(ProcessingJobModel)
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.entity_id.in_(job_entity_ids),
                    ProcessingJobModel.job_type.in_(
                        ("source_asset_download", *_RELEVANT_ASSET_JOBS)
                    ),
                )
                .order_by(
                    ProcessingJobModel.entity_id,
                    ProcessingJobModel.job_type,
                    ProcessingJobModel.created_at.desc(),
                    ProcessingJobModel.id.desc(),
                )
            ):
                latest_jobs.setdefault((job.entity_id, job.job_type), job)

        for item_id in unique_ids:
            item_asset_ids = asset_ids_by_item.get(item_id, set())
            item_source_ids = source_ids_by_item.get(item_id, set())

            source_jobs = [
                latest_jobs.get((source_id, "source_asset_download"))
                for source_id in item_source_ids
            ]
            asset_jobs = [
                latest_jobs.get((asset_id, job_type))
                for asset_id in item_asset_ids
                for job_type in _RELEVANT_ASSET_JOBS
            ]
            relevant_jobs = [job for job in (*source_jobs, *asset_jobs) if job is not None]
            analyses = [
                latest_analysis[asset_id]
                for asset_id in item_asset_ids
                if asset_id in latest_analysis
            ]

            if (
                any(job.status == "failed" for job in relevant_jobs)
                or any(analysis.status == "failed" for analysis in analyses)
                or bool(item_asset_ids & storage_failed_assets)
            ):
                statuses[item_id] = "failed"
            elif (
                bool(item_asset_ids & indexed_assets)
                or any(
                    job.job_type == "asset_index" and job.status == "completed"
                    for job in relevant_jobs
                )
            ):
                statuses[item_id] = "indexed"
            elif any(
                analysis.status == "completed" and analysis.metadata_json is not None
                for analysis in analyses
            ):
                statuses[item_id] = "metadata_ready"
            elif (
                any(analysis.status in {"pending", "running"} for analysis in analyses)
                or any(
                    job.job_type == "asset_analyze"
                    and job.status in _ACTIVE_JOB_STATUSES
                    for job in relevant_jobs
                )
            ):
                statuses[item_id] = "analyzing"
            elif bool(item_asset_ids & duplicate_assets):
                statuses[item_id] = "duplicate"
            elif bool(item_asset_ids & stored_assets):
                statuses[item_id] = "stored"

        return statuses
