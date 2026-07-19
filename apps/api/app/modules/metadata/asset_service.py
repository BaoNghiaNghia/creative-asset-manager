from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.metadata.repository import AssetMetadataRepository
from app.modules.tag.schema import AssetMetadata


def serialize_asset_metadata(metadata) -> AssetMetadata:
    return AssetMetadata(
        item_id=metadata.item_id,
        tag_ids=sorted(tag.id for tag in metadata.tags),
        rating=metadata.rating,
    )


class AssetMetadataService:
    def __init__(self, session: Session):
        self.repository = AssetMetadataRepository(session)

    def list(self, account_id: str, provider: str, item_ids: list[str]) -> list[AssetMetadata]:
        stored = {
            metadata.item_id: serialize_asset_metadata(metadata)
            for metadata in self.repository.list(account_id, provider, item_ids)
        }
        return [
            stored.get(item_id, AssetMetadata(item_id=item_id))
            for item_id in dict.fromkeys(item_ids)
        ]

    def set_rating(
        self,
        account_id: str,
        provider: str,
        item_ids: list[str],
        rating: int | None,
    ) -> list[AssetMetadata]:
        return [
            serialize_asset_metadata(metadata)
            for metadata in self.repository.set_rating(account_id, provider, item_ids, rating)
        ]
