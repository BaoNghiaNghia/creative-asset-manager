from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetStorageObjectModel(Base):
    __tablename__ = "asset_storage_objects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["assets.tenant_id", "assets.id"],
            ondelete="CASCADE",
            name="fk_asset_storage_objects_tenant_asset",
        ),
        UniqueConstraint(
            "tenant_id", "asset_id", "storage_provider",
            name="uq_asset_storage_objects_asset_provider",
        ),
        UniqueConstraint(
            "storage_provider", "remote_file_id",
            name="uq_asset_storage_objects_remote_file",
        ),
        CheckConstraint(
            "status IN ('pending', 'uploading', 'stored', 'retry', 'failed')",
            name="ck_asset_storage_objects_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_asset_storage_objects_attempt_count"),
        Index("ix_asset_storage_objects_status", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    remote_file_id: Mapped[str | None] = mapped_column(String(255))
    remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    web_url: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class MetadataSidecarExportModel(Base):
    __tablename__ = "metadata_sidecar_exports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["assets.tenant_id", "assets.id"],
            ondelete="CASCADE",
            name="fk_metadata_sidecars_tenant_asset",
        ),
        ForeignKeyConstraint(
            ["analysis_id"],
            ["asset_ai_analyses.id"],
            ondelete="CASCADE",
            name="fk_metadata_sidecars_analysis",
        ),
        UniqueConstraint(
            "analysis_id",
            "storage_provider",
            name="uq_metadata_sidecars_analysis_provider",
        ),
        UniqueConstraint(
            "storage_provider",
            "remote_file_id",
            name="uq_metadata_sidecars_remote_file",
        ),
        CheckConstraint(
            "status IN ('pending', 'exporting', 'stored', 'retry', 'failed')",
            name="ck_metadata_sidecars_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_metadata_sidecars_attempt_count"),
        Index("ix_metadata_sidecars_status", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    analysis_id: Mapped[str] = mapped_column(String(36), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    remote_file_id: Mapped[str | None] = mapped_column(String(255))
    remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(String(512))
    web_url: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
