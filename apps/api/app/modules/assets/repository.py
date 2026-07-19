from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
    SourceSyncCursorModel,
)


class AssetContentConflictError(RuntimeError):
    pass


class AssetRegistryRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_external_source(
        self,
        *,
        tenant_id: str,
        source_key: str,
        source_type: str,
        display_name: str | None = None,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> ExternalSourceModel:
        source = self.session.scalar(
            select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.source_key == source_key,
            )
        )
        if source is None:
            source = ExternalSourceModel(
                tenant_id=tenant_id,
                source_key=source_key,
                source_type=source_type,
            )
            self.session.add(source)
        source.source_type = source_type
        source.display_name = display_name
        source.source_metadata = dict(source_metadata or {})
        self.session.flush()
        return source

    def upsert_source_asset(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        external_asset_id: str,
        filename: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        source_created_at: datetime | None = None,
        source_modified_at: datetime | None = None,
        provider_checksum: str | None = None,
        provider_version: str | None = None,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> SourceAssetModel:
        source_asset = self.session.scalar(
            select(SourceAssetModel).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id == external_source_id,
                SourceAssetModel.external_asset_id == external_asset_id,
            )
        )
        if source_asset is None:
            source_asset = SourceAssetModel(
                tenant_id=tenant_id,
                external_source_id=external_source_id,
                external_asset_id=external_asset_id,
            )
            self.session.add(source_asset)
        source_asset.filename = filename
        source_asset.mime_type = mime_type
        source_asset.size_bytes = size_bytes
        source_asset.source_created_at = source_created_at
        source_asset.source_modified_at = source_modified_at
        source_asset.provider_checksum = provider_checksum
        source_asset.provider_version = provider_version
        source_asset.source_metadata = dict(source_metadata or {})
        source_asset.deleted_at = None
        self.session.flush()
        return source_asset

    def get_source_asset(self, tenant_id: str, source_asset_id: str) -> SourceAssetModel | None:
        return self.session.scalar(
            select(SourceAssetModel).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.id == source_asset_id,
            )
        )

    def mark_source_asset_hashed_version(
        self,
        *,
        tenant_id: str,
        source_asset_id: str,
        provider_checksum: str | None,
        provider_version: str | None,
    ) -> SourceAssetModel:
        source_asset = self.get_source_asset(tenant_id, source_asset_id)
        if source_asset is None:
            raise LookupError(source_asset_id)
        source_asset.hashed_provider_checksum = provider_checksum
        source_asset.hashed_provider_version = provider_version
        self.session.flush()
        return source_asset

    def find_asset_by_content_hash(self, tenant_id: str, content_hash: str) -> AssetModel | None:
        return self.session.scalar(
            select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.content_hash == content_hash,
            )
        )

    def create_asset(
        self,
        *,
        tenant_id: str,
        content_hash: str,
        analysis_image_hash: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
    ) -> AssetModel:
        try:
            with self.session.begin_nested():
                asset = AssetModel(
                    tenant_id=tenant_id,
                    content_hash=content_hash,
                    analysis_image_hash=analysis_image_hash,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                )
                self.session.add(asset)
                self.session.flush()
            return asset
        except IntegrityError as exc:
            raise AssetContentConflictError(content_hash) from exc

    def link_source_asset(
        self, *, tenant_id: str, asset_id: str, source_asset_id: str
    ) -> AssetSourceLinkModel:
        existing = self.session.scalar(
            select(AssetSourceLinkModel).where(
                AssetSourceLinkModel.asset_id == asset_id,
                AssetSourceLinkModel.source_asset_id == source_asset_id,
            )
        )
        if existing is not None:
            return existing
        self.session.execute(
            delete(AssetSourceLinkModel).where(
                AssetSourceLinkModel.source_asset_id == source_asset_id,
                AssetSourceLinkModel.asset_id != asset_id,
            )
        )
        try:
            with self.session.begin_nested():
                link = AssetSourceLinkModel(
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    source_asset_id=source_asset_id,
                )
                self.session.add(link)
                self.session.flush()
            return link
        except IntegrityError:
            existing = self.session.scalar(
                select(AssetSourceLinkModel).where(
                    AssetSourceLinkModel.asset_id == asset_id,
                    AssetSourceLinkModel.source_asset_id == source_asset_id,
                )
            )
            if existing is None:
                raise
            return existing

    def find_linked_asset(self, tenant_id: str, source_asset_id: str) -> AssetModel | None:
        return self.session.scalar(
            select(AssetModel)
            .join(AssetSourceLinkModel, AssetSourceLinkModel.asset_id == AssetModel.id)
            .where(
                AssetModel.tenant_id == tenant_id,
                AssetSourceLinkModel.source_asset_id == source_asset_id,
            )
        )

    def mark_source_asset_deleted(
        self, *, tenant_id: str, source_asset_id: str
    ) -> SourceAssetModel:
        source_asset = self.get_source_asset(tenant_id, source_asset_id)
        if source_asset is None:
            raise LookupError(source_asset_id)
        source_asset.deleted_at = datetime.now(timezone.utc)
        self.session.flush()
        return source_asset

    def save_sync_cursor(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        cursor_value: str,
        cursor_key: str = "changes",
    ) -> SourceSyncCursorModel:
        cursor = self.session.scalar(
            select(SourceSyncCursorModel).where(
                SourceSyncCursorModel.tenant_id == tenant_id,
                SourceSyncCursorModel.external_source_id == external_source_id,
                SourceSyncCursorModel.cursor_key == cursor_key,
            )
        )
        if cursor is None:
            cursor = SourceSyncCursorModel(
                tenant_id=tenant_id,
                external_source_id=external_source_id,
                cursor_key=cursor_key,
                cursor_value=cursor_value,
            )
            self.session.add(cursor)
        cursor.cursor_value = cursor_value
        self.session.flush()
        return cursor

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
