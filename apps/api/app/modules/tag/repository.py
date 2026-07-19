from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.tag.model import TagModel


SYSTEM_TAGS = (
    {"id": "public", "name": "Public", "color": "#26a269", "group_key": "visibility", "is_system": True},
    {"id": "draft", "name": "Draft", "color": "#e5a50a", "group_key": "visibility", "is_system": True},
)


class TagRepository:
    def __init__(self, session: Session):
        self.session = session

    def seed_system_tags(self) -> None:
        for values in SYSTEM_TAGS:
            tag = self.session.get(TagModel, values["id"])
            if tag is None:
                self.session.add(TagModel(**values))
                continue
            for key, value in values.items():
                setattr(tag, key, value)

    def list(self) -> list[TagModel]:
        return list(self.session.scalars(select(TagModel).order_by(TagModel.name)))

    def get(self, tag_id: str) -> TagModel | None:
        return self.session.get(TagModel, tag_id)
