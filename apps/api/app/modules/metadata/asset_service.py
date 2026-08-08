from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.assets.status_service import (
    AssetProcessingStatus,
    AssetProcessingStatusService,
)
from app.modules.metadata.repository import AssetMetadataRepository
from app.modules.tag.schema import AssetMetadata


def serialize_asset_metadata(
    metadata,
    processing_status: AssetProcessingStatus = "discovered",
) -> AssetMetadata:
    return AssetMetadata(
        item_id=metadata.item_id,
        tag_ids=sorted(tag.id for tag in metadata.tags),
        rating=metadata.rating,
        processing_status=processing_status,
    )


class AssetMetadataService:
    def __init__(self, session: Session):
        self.repository = AssetMetadataRepository(session)
        self.processing_statuses = AssetProcessingStatusService(session)

    def list(
        self,
        account_id: str,
        provider: str,
        item_ids: list[str],
        *,
        processing_tenant_id: str | None = None,
        external_source_id: str | None = None,
    ) -> list[AssetMetadata]:
        statuses = self.processing_statuses.list(
            processing_tenant_id or account_id,
            provider,
            item_ids,
            external_source_id=external_source_id,
        )
        stored = {
            metadata.item_id: serialize_asset_metadata(
                metadata,
                statuses[metadata.item_id],
            )
            for metadata in self.repository.list(account_id, provider, item_ids)
        }
        return [
            stored.get(
                item_id,
                AssetMetadata(
                    item_id=item_id,
                    processing_status=statuses[item_id],
                ),
            )
            for item_id in dict.fromkeys(item_ids)
        ]

    def set_rating(
        self,
        account_id: str,
        provider: str,
        item_ids: list[str],
        rating: int | None,
    ) -> list[AssetMetadata]:
        metadata = self.repository.set_rating(account_id, provider, item_ids, rating)
        statuses = self.processing_statuses.list(account_id, provider, item_ids)
        return [
            serialize_asset_metadata(item, statuses[item.item_id])
            for item in metadata
        ]
