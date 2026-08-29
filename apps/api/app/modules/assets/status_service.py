from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
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
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.search.operations_model import (
    SearchOperationItemModel,
    SearchOperationRunModel,
)
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.video_search.model import VideoAnalysisRunModel

AssetProcessingStatus = Literal[
    "discovered",
    "stored",
    "analyzing",
    "metadata_ready",
    "search_pending",
    "indexing",
    "indexed",
    "search_failed",
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
_RELEVANT_VIDEO_JOBS = {
    "video_analyze",
    "video_search_index",
}


@dataclass(frozen=True, slots=True)
class AssetSourceIdentity:
    source_asset_id: str
    internal_asset_id: str | None
    external_source_id: str


class AssetProcessingStatusService:
    """Build read-only UI status projections from authoritative PostgreSQL state."""

    def __init__(self, session: Session):
        self.session = session

    def list_source_identities(
        self,
        tenant_id: str,
        provider: str,
        item_ids: list[str],
        *,
        external_source_id: str | None = None,
    ) -> dict[str, list[AssetSourceIdentity]]:
        """Resolve registry identities for provider items with one tenant-scoped query."""
        unique_ids = tuple(dict.fromkeys(item_ids))
        if not unique_ids:
            return {}

        source_type = _SOURCE_TYPES.get(provider)
        if source_type is None:
            return {}

        statement = (
            select(
                SourceAssetModel.external_asset_id,
                SourceAssetModel.id,
                AssetSourceLinkModel.asset_id,
                SourceAssetModel.external_source_id,
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
        )
        if external_source_id is not None:
            statement = statement.where(
                SourceAssetModel.external_source_id == external_source_id
            )

        identities: dict[str, list[AssetSourceIdentity]] = defaultdict(list)
        for item_id, source_asset_id, asset_id, resolved_source_id in self.session.execute(
            statement
        ):
            identities[item_id].append(
                AssetSourceIdentity(
                    source_asset_id=source_asset_id,
                    internal_asset_id=asset_id,
                    external_source_id=resolved_source_id,
                )
            )
        return dict(identities)

    def list(
        self,
        tenant_id: str,
        provider: str,
        item_ids: list[str],
        *,
        external_source_id: str | None = None,
    ) -> dict[str, AssetProcessingStatus]:
        unique_ids = tuple(dict.fromkeys(item_ids))
        statuses: dict[str, AssetProcessingStatus] = {
            item_id: "discovered" for item_id in unique_ids
        }
        if not unique_ids:
            return statuses

        identities = self.list_source_identities(
            tenant_id,
            provider,
            list(unique_ids),
            external_source_id=external_source_id,
        )
        if not identities:
            return statuses

        source_ids_by_item: dict[str, set[str]] = defaultdict(set)
        asset_ids_by_item: dict[str, set[str]] = defaultdict(set)
        for item_id, item_identities in identities.items():
            for identity in item_identities:
                source_ids_by_item[item_id].add(identity.source_asset_id)
                if identity.internal_asset_id:
                    asset_ids_by_item[item_id].add(identity.internal_asset_id)

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
        latest_video_runs: dict[str, VideoAnalysisRunModel] = {}
        pipeline_ids_by_source_asset: dict[str, set[str]] = defaultdict(set)
        pipeline_ids_by_asset: dict[str, set[str]] = defaultdict(set)

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

        if source_asset_ids:
            for pipeline_id, source_asset_id, asset_id in self.session.execute(
                select(
                    AssetPipelineModel.id,
                    AssetPipelineModel.source_asset_id,
                    AssetPipelineModel.asset_id,
                ).where(
                    AssetPipelineModel.tenant_id == tenant_id,
                    AssetPipelineModel.source_asset_id.in_(source_asset_ids),
                )
            ):
                if source_asset_id:
                    pipeline_ids_by_source_asset[source_asset_id].add(pipeline_id)
                if asset_id:
                    pipeline_ids_by_asset[asset_id].add(pipeline_id)

            for run in self.session.scalars(
                select(VideoAnalysisRunModel)
                .where(
                    VideoAnalysisRunModel.tenant_id == tenant_id,
                    VideoAnalysisRunModel.source_asset_id.in_(source_asset_ids),
                )
                .order_by(
                    VideoAnalysisRunModel.source_asset_id,
                    VideoAnalysisRunModel.created_at.desc(),
                    VideoAnalysisRunModel.id.desc(),
                )
            ):
                latest_video_runs.setdefault(run.source_asset_id, run)

        pipeline_ids = {
            pipeline_id
            for values in (
                *pipeline_ids_by_source_asset.values(),
                *pipeline_ids_by_asset.values(),
            )
            for pipeline_id in values
        }
        video_run_ids = {
            run.id for run in latest_video_runs.values()
        }
        job_entity_ids = tuple(
            sorted({*asset_ids, *source_asset_ids, *pipeline_ids, *video_run_ids})
        )
        if job_entity_ids:
            for job in self.session.scalars(
                select(ProcessingJobModel)
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.entity_id.in_(job_entity_ids),
                    ProcessingJobModel.job_type.in_(
                        ("source_asset_download", *_RELEVANT_ASSET_JOBS, *_RELEVANT_VIDEO_JOBS)
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
            source_pipeline_ids = {
                pipeline_id
                for source_id in item_source_ids
                for pipeline_id in pipeline_ids_by_source_asset.get(source_id, set())
            }
            asset_pipeline_ids = {
                pipeline_id
                for asset_id in item_asset_ids
                for pipeline_id in pipeline_ids_by_asset.get(asset_id, set())
            }
            # A deduplicated managed asset can be linked to several source items.
            # Do not let a sibling source's failed pipeline contaminate this item.
            # Asset-level lookup remains a fallback for legacy pipelines without a
            # source_asset_id.
            item_pipeline_ids = source_pipeline_ids or asset_pipeline_ids
            pipeline_jobs = [
                latest_jobs.get((pipeline_id, job_type))
                for pipeline_id in item_pipeline_ids
                for job_type in _RELEVANT_ASSET_JOBS
            ]
            relevant_jobs = [
                job
                for job in (*source_jobs, *asset_jobs, *pipeline_jobs)
                if job is not None
            ]
            analyses = [
                latest_analysis[asset_id]
                for asset_id in item_asset_ids
                if asset_id in latest_analysis
            ]
            video_runs = [
                latest_video_runs[source_id]
                for source_id in item_source_ids
                if source_id in latest_video_runs
            ]
            video_jobs = [
                job
                for job in (
                    *(latest_jobs.get((source_id, "video_analyze")) for source_id in item_source_ids),
                    *(latest_jobs.get((run.id, "video_search_index")) for run in video_runs),
                )
                if job is not None
            ]

            if any(
                job.job_type in {"asset_index", "video_search_index"}
                and job.status == "failed"
                for job in (*relevant_jobs, *video_jobs)
            ):
                statuses[item_id] = "search_failed"
            elif (
                any(job.status == "failed" for job in relevant_jobs)
                or any(
                    job.job_type == "video_analyze" and job.status == "failed"
                    for job in video_jobs
                )
                or any(analysis.status == "failed" for analysis in analyses)
                or any(run.status == "failed" for run in video_runs)
                or bool(item_asset_ids & storage_failed_assets)
            ):
                statuses[item_id] = "failed"
            elif any(
                job.job_type in {"asset_index", "video_search_index"}
                and job.status == "processing"
                for job in (*relevant_jobs, *video_jobs)
            ):
                statuses[item_id] = "indexing"
            elif (
                bool(item_asset_ids & indexed_assets)
                or any(
                    job.job_type == "asset_index" and job.status == "completed"
                    for job in relevant_jobs
                )
                or any(
                    job.job_type == "video_search_index" and job.status == "completed"
                    for job in video_jobs
                )
            ):
                statuses[item_id] = "indexed"
            elif any(
                job.job_type in {"asset_index", "video_search_index"}
                and job.status in {"pending", "retry"}
                for job in (*relevant_jobs, *video_jobs)
            ):
                statuses[item_id] = "search_pending"
            elif any(
                analysis.status == "completed" and analysis.metadata_json is not None
                for analysis in analyses
            ) or any(run.status == "completed" for run in video_runs):
                statuses[item_id] = "search_pending"
            elif (
                any(run.status in {"pending", "preparing", "analyzing"} for run in video_runs)
                or any(
                    job.job_type == "video_analyze"
                    and job.status in _ACTIVE_JOB_STATUSES
                    for job in video_jobs
                )
            ):
                statuses[item_id] = "analyzing"
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
