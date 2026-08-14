from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel
from app.modules.external_ingestion.model import AssetIngestionItemModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.state import PipelineState
from app.modules.storage.model import AssetStorageObjectModel

logger = logging.getLogger(__name__)


@dataclass
class ManagedStorageSelfIngestionRepairResult:
    selected: int = 0
    self_ingested: int = 0
    repairable: int = 0
    repaired_links: int = 0
    removed_source_assets: int = 0
    skipped_only_source: int = 0
    skipped_ambiguous: int = 0
    failed: int = 0

    def document(self) -> dict[str, int]:
        return {
            "selected": self.selected,
            "self_ingested": self.self_ingested,
            "repairable": self.repairable,
            "repaired_links": self.repaired_links,
            "removed_source_assets": self.removed_source_assets,
            "skipped_only_source": self.skipped_only_source,
            "skipped_ambiguous": self.skipped_ambiguous,
            "failed": self.failed,
        }


class ManagedStorageSelfIngestionRepairService:
    """Remove only registry links that point back to CAM's staging copies.

    The service deliberately has no storage provider dependency: a repair never
    deletes a remote Drive file or an AssetStorageObjectModel row.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def execute(
        self, *, tenant_id: str, limit: int = 100, dry_run: bool = True
    ) -> ManagedStorageSelfIngestionRepairResult:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        result = ManagedStorageSelfIngestionRepairResult()
        with self._session_factory() as session:
            candidate_ids = list(
                session.scalars(
                    self._candidate_statement(tenant_id=tenant_id, limit=limit)
                )
            )
        result.selected = len(candidate_ids)
        logger.info(
            "managed_storage_self_ingestion_repair_started tenant_id=%s selected=%s dry_run=%s",
            tenant_id,
            result.selected,
            dry_run,
        )
        for storage_id in candidate_ids:
            try:
                self._repair_one(
                    tenant_id=tenant_id,
                    storage_id=str(storage_id),
                    dry_run=dry_run,
                    result=result,
                )
            except Exception:
                result.failed += 1
                logger.exception(
                    "managed_storage_self_ingestion_repair_failed tenant_id=%s storage_id=%s",
                    tenant_id,
                    storage_id,
                )
        return result

    @staticmethod
    def _candidate_statement(*, tenant_id: str, limit: int):
        candidate_exists = exists(
            select(1)
            .select_from(SourceAssetModel)
            .join(
                AssetSourceLinkModel,
                and_(
                    AssetSourceLinkModel.tenant_id == SourceAssetModel.tenant_id,
                    AssetSourceLinkModel.source_asset_id == SourceAssetModel.id,
                ),
            )
            .where(
                SourceAssetModel.tenant_id == AssetStorageObjectModel.tenant_id,
                SourceAssetModel.external_asset_id
                == AssetStorageObjectModel.remote_file_id,
            )
            .correlate(AssetStorageObjectModel)
        )
        return (
            select(AssetStorageObjectModel.id)
            .where(
                AssetStorageObjectModel.tenant_id == tenant_id,
                AssetStorageObjectModel.storage_provider == "google_drive_managed",
                AssetStorageObjectModel.remote_file_id.is_not(None),
                candidate_exists,
            )
            .order_by(AssetStorageObjectModel.stored_at, AssetStorageObjectModel.id)
            .limit(limit)
        )

    def _repair_one(
        self,
        *,
        tenant_id: str,
        storage_id: str,
        dry_run: bool,
        result: ManagedStorageSelfIngestionRepairResult,
    ) -> None:
        with self._session_factory() as session:
            storage = session.scalar(
                select(AssetStorageObjectModel)
                .where(
                    AssetStorageObjectModel.id == storage_id,
                    AssetStorageObjectModel.tenant_id == tenant_id,
                    AssetStorageObjectModel.storage_provider == "google_drive_managed",
                )
                .with_for_update()
            )
            if storage is None or not storage.remote_file_id:
                return
            sources = list(
                session.scalars(
                    select(SourceAssetModel)
                    .where(
                        SourceAssetModel.tenant_id == tenant_id,
                        SourceAssetModel.external_asset_id == storage.remote_file_id,
                    )
                    .with_for_update()
                )
            )
            if not sources:
                session.commit()
                return
            # A storage object can have multiple self-ingested registry rows.
            # Validate the entire set before making any change so a malformed
            # member can never result in a partial repair.
            if any(
                not self._has_managed_root_evidence(storage, source)
                for source in sources
            ):
                result.skipped_ambiguous += 1
                session.commit()
                return
            managed_source_ids = [source.id for source in sources]
            managed_links: list[AssetSourceLinkModel] = []
            for source in sources:
                links = list(
                    session.scalars(
                        select(AssetSourceLinkModel)
                        .where(
                            AssetSourceLinkModel.tenant_id == tenant_id,
                            AssetSourceLinkModel.source_asset_id == source.id,
                        )
                        .with_for_update()
                    )
                )
                if len(links) != 1 or links[0].asset_id != storage.asset_id:
                    result.skipped_ambiguous += 1
                    session.commit()
                    return
                managed_links.append(links[0])
            restrictive_ingestion_references = list(
                session.scalars(
                    select(AssetIngestionItemModel.id)
                    .where(
                        AssetIngestionItemModel.tenant_id == tenant_id,
                        AssetIngestionItemModel.source_asset_id.in_(managed_source_ids),
                    )
                    .with_for_update()
                )
            )
            # asset_ingestion_items has an explicit RESTRICT FK to source assets.
            # Do not invalidate an external-ingestion audit trail to repair a
            # managed-storage self-reference.
            if restrictive_ingestion_references:
                result.skipped_ambiguous += 1
                session.commit()
                return
            result.self_ingested += 1
            other_link_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AssetSourceLinkModel)
                    .where(
                        AssetSourceLinkModel.tenant_id == tenant_id,
                        AssetSourceLinkModel.asset_id == storage.asset_id,
                        ~AssetSourceLinkModel.source_asset_id.in_(managed_source_ids),
                    )
                )
                or 0
            )
            if other_link_count < 1:
                result.skipped_only_source += 1
                logger.info(
                    "managed_storage_self_ingestion_skipped_only_source tenant_id=%s asset_id=%s managed_source_count=%s",
                    tenant_id,
                    storage.asset_id,
                    len(managed_source_ids),
                )
                session.commit()
                return
            pipelines = list(
                session.scalars(
                    select(AssetPipelineModel)
                    .where(
                        AssetPipelineModel.tenant_id == tenant_id,
                        AssetPipelineModel.source_asset_id.in_(managed_source_ids),
                    )
                    .with_for_update()
                )
            )
            # source_asset_id is the live source lookup used by pipeline handlers.
            # A non-terminal pipeline could still need it, so preserve all rows and
            # leave this storage object untouched until it is safe to repair.
            if any(
                pipeline.state != PipelineState.COMPLETED.value
                for pipeline in pipelines
            ):
                result.skipped_ambiguous += 1
                session.commit()
                return
            result.repairable += 1
            if dry_run:
                session.commit()
                return
            # Completed pipelines are durable history.  origin_type/origin_id
            # preserve provenance after detaching the deleted managed source.
            if pipelines:
                session.execute(
                    update(AssetPipelineModel)
                    .where(
                        AssetPipelineModel.tenant_id == tenant_id,
                        AssetPipelineModel.source_asset_id.in_(managed_source_ids),
                    )
                    .values(source_asset_id=None)
                )
                session.flush()
            local_repaired_links = len(managed_links)
            local_removed_source_assets = 0
            for link in managed_links:
                session.delete(link)
            session.flush()
            for source in sources:
                remaining = int(
                    session.scalar(
                        select(func.count())
                        .select_from(AssetSourceLinkModel)
                        .where(
                            AssetSourceLinkModel.tenant_id == tenant_id,
                            AssetSourceLinkModel.source_asset_id == source.id,
                        )
                    )
                    or 0
                )
                if remaining == 0:
                    session.delete(source)
                    local_removed_source_assets += 1
            session.commit()
            result.repaired_links += local_repaired_links
            result.removed_source_assets += local_removed_source_assets
            logger.info(
                "managed_storage_self_ingestion_repaired tenant_id=%s asset_id=%s managed_source_count=%s",
                tenant_id,
                storage.asset_id,
                len(managed_source_ids),
            )

    def _has_managed_root_evidence(
        self, storage: AssetStorageObjectModel, source: SourceAssetModel
    ) -> bool:
        root_id = str(storage.remote_folder_id or "").strip()
        configured_root = str(self._settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or "").strip()
        if configured_root and root_id != configured_root:
            return False
        parents = (source.source_metadata or {}).get("parents") or []
        return bool(root_id and root_id in {str(parent) for parent in parents})
