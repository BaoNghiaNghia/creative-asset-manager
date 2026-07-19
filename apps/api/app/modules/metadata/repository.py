from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.metadata.model import AssetMetadataModel
from app.modules.tag.model import TagModel


class AssetMetadataRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self, account_id: str, provider: str, item_ids: list[str]) -> list[AssetMetadataModel]:
        if not item_ids:
            return []
        statement = (
            select(AssetMetadataModel)
            .options(selectinload(AssetMetadataModel.tags))
            .where(
                AssetMetadataModel.account_id == account_id,
                AssetMetadataModel.provider == provider,
                AssetMetadataModel.item_id.in_(item_ids),
            )
        )
        return list(self.session.scalars(statement))

    def get_or_create(self, account_id: str, provider: str, item_id: str) -> AssetMetadataModel:
        statement = select(AssetMetadataModel).where(
            AssetMetadataModel.account_id == account_id,
            AssetMetadataModel.provider == provider,
            AssetMetadataModel.item_id == item_id,
        )
        metadata = self.session.scalar(statement)
        if metadata is None:
            metadata = AssetMetadataModel(account_id=account_id, provider=provider, item_id=item_id)
            self.session.add(metadata)
            self.session.flush()
        return metadata

    def assign_tag(
        self, account_id: str, provider: str, item_ids: list[str], tag: TagModel
    ) -> list[AssetMetadataModel]:
        results = []
        for item_id in dict.fromkeys(item_ids):
            metadata = self.get_or_create(account_id, provider, item_id)
            if tag.group_key:
                metadata.tags = [current for current in metadata.tags if current.group_key != tag.group_key]
            if all(current.id != tag.id for current in metadata.tags):
                metadata.tags.append(tag)
            results.append(metadata)
        self.session.commit()
        return results

    def set_rating(
        self, account_id: str, provider: str, item_ids: list[str], rating: int | None
    ) -> list[AssetMetadataModel]:
        results = []
        for item_id in dict.fromkeys(item_ids):
            metadata = self.get_or_create(account_id, provider, item_id)
            metadata.rating = rating
            results.append(metadata)
        self.session.commit()
        return results
