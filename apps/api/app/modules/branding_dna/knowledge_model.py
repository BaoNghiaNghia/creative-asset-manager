"""Per-shop upload corpus with immutable source bytes and versioned chunk evidence."""
from __future__ import annotations

from uuid import uuid4
from datetime import datetime
from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer,
    LargeBinary, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandKnowledgeDocumentModel(Base):
    __tablename__ = "bc_knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "shop_id", "id", name="uq_bc_rag_document_scope"),
        ForeignKeyConstraint(["shop_id"], ["bc_shops.id"], name="fk_bc_rag_document_shop",
                             ondelete="RESTRICT"),
        Index("ix_bc_rag_document_shop_status", "tenant_id", "shop_id", "status", "created_at"),
        CheckConstraint("byte_count BETWEEN 1 AND 2097152", name="ck_bc_rag_document_bytes"),
        CheckConstraint("status IN ('pending_review','approved','rejected','archived')",
                        name="ck_bc_rag_document_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    filename: Mapped[str] = mapped_column(String(150), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    source_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False)
    text_length: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    original_file: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    review_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrandKnowledgeChunkModel(Base):
    __tablename__ = "bc_knowledge_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "shop_id", "document_id"],
            ["bc_knowledge_documents.tenant_id", "bc_knowledge_documents.shop_id",
             "bc_knowledge_documents.id"],
            name="fk_bc_rag_chunk_document_scope", ondelete="RESTRICT",
        ),
        UniqueConstraint("document_id", "ordinal", name="uq_bc_rag_chunk_order"),
        Index("ix_bc_rag_chunks_shop_document", "tenant_id", "shop_id", "document_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
