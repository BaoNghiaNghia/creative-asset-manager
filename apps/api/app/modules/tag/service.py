from sqlalchemy.orm import Session

from app.modules.metadata.asset_service import serialize_asset_metadata
from app.modules.metadata.repository import AssetMetadataRepository
from app.modules.tag.repository import TagRepository
from app.modules.tag.schema import AssetMetadata, Tag


class UnknownTagError(ValueError):
    pass


class TagService:
    def __init__(self, session: Session):
        self.session = session
        self.tags = TagRepository(session)
        self.metadata = AssetMetadataRepository(session)

    def list_tags(self) -> list[Tag]:
        return [
            Tag(
                id=tag.id,
                name=tag.name,
                color=tag.color,
                group_key=tag.group_key,
                is_system=tag.is_system,
            )
            for tag in self.tags.list()
        ]

    def assign(
        self,
        account_id: str,
        item_ids: list[str],
        tag_id: str,
        provider: str = "google-drive",
    ) -> list[AssetMetadata]:
        tag = self.tags.get(tag_id)
        if tag is None:
            raise UnknownTagError(f"Unknown tag: {tag_id}")
        return [
            serialize_asset_metadata(metadata)
            for metadata in self.metadata.assign_tag(
                account_id, provider, item_ids, tag
            )
        ]
