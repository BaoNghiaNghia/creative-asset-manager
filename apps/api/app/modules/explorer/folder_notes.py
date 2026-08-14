from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
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


async def resolve_note_owner_from_nodes(
    requested_folder: Any,
    get_node: Callable[[str], Awaitable[Any]],
    max_depth: int = 64,
) -> Any | None:
    """Return the nearest product root while walking a provider parent chain."""
    node = requested_folder
    visited: set[str] = set()
    for _depth in range(max_depth):
        node_id = str(getattr(node, "id", ""))
        if not node_id or node_id in visited:
            return None
        visited.add(node_id)
        if product_folder_kind(str(getattr(node, "name", ""))):
            return node
        parent_id = getattr(node, "parent_id", None)
        if not parent_id or parent_id == "root":
            return None
        node = await get_node(str(parent_id))
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
