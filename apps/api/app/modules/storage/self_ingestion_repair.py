from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel
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
                    select(AssetStorageObjectModel.id)
                    .join(
                        SourceAssetModel,
                        and_(
                            SourceAssetModel.tenant_id == AssetStorageObjectModel.tenant_id,
                            SourceAssetModel.external_asset_id
                            == AssetStorageObjectModel.remote_file_id,
                        ),
                    )
                    .join(
                        AssetSourceLinkModel,
                        and_(
                            AssetSourceLinkModel.tenant_id == SourceAssetModel.tenant_id,
                            AssetSourceLinkModel.source_asset_id == SourceAssetModel.id,
                        ),
                    )
                    .where(
                        AssetStorageObjectModel.tenant_id == tenant_id,
                        AssetStorageObjectModel.storage_provider == "google_drive_managed",
                        AssetStorageObjectModel.remote_file_id.is_not(None),
                    )
                    .distinct()
                    .order_by(AssetStorageObjectModel.stored_at, AssetStorageObjectModel.id)
                    .limit(limit)
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
            if len(sources) != 1:
                result.skipped_ambiguous += 1
                session.commit()
                return
            source = sources[0]
            if not self._has_managed_root_evidence(storage, source):
                result.skipped_ambiguous += 1
                session.commit()
                return
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
            result.self_ingested += 1
            other_link_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AssetSourceLinkModel)
                    .where(
                        AssetSourceLinkModel.tenant_id == tenant_id,
                        AssetSourceLinkModel.asset_id == storage.asset_id,
                        AssetSourceLinkModel.source_asset_id != source.id,
                    )
                )
                or 0
            )
            if other_link_count < 1:
                result.skipped_only_source += 1
                logger.info(
                    "managed_storage_self_ingestion_skipped_only_source tenant_id=%s asset_id=%s source_asset_id=%s",
                    tenant_id,
                    storage.asset_id,
                    source.id,
                )
                session.commit()
                return
            result.repairable += 1
            if dry_run:
                session.commit()
                return
            session.delete(links[0])
            session.flush()
            result.repaired_links += 1
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
                result.removed_source_assets += 1
            session.commit()
            logger.info(
                "managed_storage_self_ingestion_repaired tenant_id=%s asset_id=%s source_asset_id=%s",
                tenant_id,
                storage.asset_id,
                source.id,
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
