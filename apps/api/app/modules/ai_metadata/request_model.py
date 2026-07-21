from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AiAnalysisRequestModel(Base):
    __tablename__ = "ai_analysis_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key",
            name="uq_ai_analysis_requests_tenant_key",
        ),
        UniqueConstraint(
            "tenant_id", "id",
            name="uq_ai_analysis_requests_tenant_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metadata_profile_id"],
            ["metadata_profiles.tenant_id", "metadata_profiles.id"],
            ondelete="RESTRICT",
            name="fk_ai_analysis_requests_tenant_profile",
        ),
        CheckConstraint(
            "status IN ('accepted', 'cancelled')",
            name="ck_ai_analysis_requests_status",
        ),
        CheckConstraint(
            "processing_mode IN ('single', 'batch')",
            name="ck_ai_analysis_requests_mode",
        ),
        CheckConstraint(
            "item_count > 0",
            name="ck_ai_analysis_requests_item_count",
        ),
        Index(
            "ix_ai_analysis_requests_tenant_created",
            "tenant_id", "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    metadata_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metadata_profile: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_profile_version: Mapped[str | None] = mapped_column(String(100))
    ai_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    ai_model: Mapped[str] = mapped_column(String(255), nullable=False)
    processing_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="accepted")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warning: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_by: Mapped[str | None] = mapped_column(String(255))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AiAnalysisRequestItemModel(Base):
    __tablename__ = "ai_analysis_request_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["ai_analysis_requests.tenant_id", "ai_analysis_requests.id"],
            ondelete="CASCADE",
            name="fk_ai_analysis_request_items_tenant_request",
        ),
        ForeignKeyConstraint(
            ["analysis_id"], ["asset_ai_analyses.id"],
            ondelete="RESTRICT",
            name="fk_ai_analysis_request_items_analysis",
        ),
        ForeignKeyConstraint(
            ["processing_job_id"], ["processing_jobs.id"],
            ondelete="SET NULL",
            name="fk_ai_analysis_request_items_job",
        ),
        UniqueConstraint(
            "request_id", "requested_asset_id",
            name="uq_ai_analysis_request_items_asset",
        ),
        CheckConstraint(
            "acceptance_status IN "
            "('accepted', 'already_exists', 'invalid_asset', 'unauthorized', "
            "'provider_unavailable', 'budget_preflight_failed')",
            name="ck_ai_analysis_request_items_acceptance",
        ),
        Index(
            "ix_ai_analysis_request_items_request",
            "request_id", "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    analysis_id: Mapped[str | None] = mapped_column(String(36))
    processing_job_id: Mapped[str | None] = mapped_column(String(36))
    acceptance_status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
