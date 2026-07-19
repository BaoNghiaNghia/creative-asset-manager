from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.model import ExternalSourceModel, SourceAssetModel, SourceSyncCursorModel
from app.modules.assets.repository import AssetRegistryRepository


class SourceSyncRepository:
    def __init__(self, session: Session):
        self.session = session
        self.assets = AssetRegistryRepository(session)

    def get_source(self, tenant_id: str, source_id: str) -> ExternalSourceModel | None:
        return self.session.scalar(
            select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.id == source_id,
            )
        )

    def get_source_asset_by_external_id(
        self, tenant_id: str, source_id: str, external_asset_id: str
    ) -> SourceAssetModel | None:
        return self.session.scalar(
            select(SourceAssetModel).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id == source_id,
                SourceAssetModel.external_asset_id == external_asset_id,
            )
        )

    def get_cursor(
        self, tenant_id: str, source_id: str, cursor_key: str = "changes"
    ) -> str | None:
        cursor = self.session.scalar(
            select(SourceSyncCursorModel).where(
                SourceSyncCursorModel.tenant_id == tenant_id,
                SourceSyncCursorModel.external_source_id == source_id,
                SourceSyncCursorModel.cursor_key == cursor_key,
            )
        )
        return cursor.cursor_value if cursor else None

    def list_external_ids(self, tenant_id: str, source_id: str) -> set[str]:
        return set(
            self.session.scalars(
                select(SourceAssetModel.external_asset_id).where(
                    SourceAssetModel.tenant_id == tenant_id,
                    SourceAssetModel.external_source_id == source_id,
                    SourceAssetModel.deleted_at.is_(None),
                )
            )
        )
