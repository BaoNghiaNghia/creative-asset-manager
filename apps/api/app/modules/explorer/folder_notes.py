from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

FOLDER_NOTE_MAX_LENGTH = 50_000
_AMAZON = re.compile(r"^Amazon\s*-\s*([A-Za-z0-9]{10})(?:\s*-\s*(.*))?$", re.IGNORECASE)
_ETSY = re.compile(r"^listing\s*-\s*(\d+)(?:\s*-\s*(.*))?$", re.IGNORECASE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def product_folder_kind(name: str) -> str | None:
    value = (name or "").strip()
    if _AMAZON.fullmatch(value):
        return "amazon"
    if _ETSY.fullmatch(value):
        return "etsy"
    return None


class FolderNoteModel(Base):
    __tablename__ = "folder_notes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE",
            name="fk_folder_notes_tenant_source",
        ),
        UniqueConstraint(
            "tenant_id", "external_source_id", "folder_external_id",
            name="uq_folder_notes_folder_identity",
        ),
        Index("ix_folder_notes_tenant_source_folder", "tenant_id", "external_source_id", "folder_external_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    folder_external_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
