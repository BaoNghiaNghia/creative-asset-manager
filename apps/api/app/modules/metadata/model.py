from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.tag.model import TagModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


asset_tag_assignments = Table(
    "asset_tag_assignments",
    Base.metadata,
    Column("asset_metadata_id", ForeignKey("asset_metadata.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class AssetMetadataModel(Base):
    __tablename__ = "asset_metadata"
    __table_args__ = (
        UniqueConstraint("account_id", "provider", "item_id", name="uq_asset_identity"),
        CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name="ck_asset_rating"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    tags: Mapped[list[TagModel]] = relationship(secondary=asset_tag_assignments, lazy="selectin")
