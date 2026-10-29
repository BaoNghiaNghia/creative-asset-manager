"""Typography V2 metadata; physical image bytes stay in the existing Reference Library.

Never change the profile on a shared rrugc_reference_assets row merely because
its bytes are also referenced by typography. This link is tenant-scoped and
preserves UGC behavior, deduplication, and reference attribution independently.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKeyConstraint, Index,
    Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RrugcTypographyReferenceModel(Base):
    __tablename__ = "rrugc_typography_references"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_typography_reference_tenant_id"),
        UniqueConstraint("tenant_id", "content_hash", name="uq_typography_reference_tenant_hash"),
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["rrugc_reference_assets.tenant_id", "rrugc_reference_assets.id"],
            name="fk_typography_reference_asset_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_type IN ('upload','scout','internal')",
            name="ck_typography_reference_source_type",
        ),
        CheckConstraint(
            "source_rights_status IN ('owned','licensed','permitted_reference','unknown','blocked')",
            name="ck_typography_reference_rights",
        ),
        CheckConstraint(
            "privacy IN ('private','workspace')",
            name="ck_typography_reference_privacy",
        ),
        CheckConstraint(
            "scope IN ('workspace','campaign','keyword','skill')",
            name="ck_typography_reference_scope",
        ),
        CheckConstraint("influence = 'balanced'", name="ck_typography_reference_balanced"),
        CheckConstraint(
            "preference IN ('positive','negative','neutral')",
            name="ck_typography_reference_preference",
        ),
        CheckConstraint(
            "analysis_status IN ('pending','running','ready','failed','needs_review')",
            name="ck_typography_reference_analysis_status",
        ),
        CheckConstraint(
            "editorial_status IN ('candidate','approved','rejected','archived')",
            name="ck_typography_reference_editorial_status",
        ),
        CheckConstraint("priority >= -100 AND priority <= 100", name="ck_typography_reference_priority"),
        Index(
            "ix_typography_reference_library",
            "tenant_id", "editorial_status", "analysis_status", "updated_at",
        ),
        Index(
            "ix_typography_reference_scope",
            "tenant_id", "scope", "campaign_id", "keyword_id",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    thumbnail_asset_id: Mapped[str | None] = mapped_column(String(36))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False, default="upload")
    source_url: Mapped[str | None] = mapped_column(Text)
    source_platform: Mapped[str | None] = mapped_column(String(64))
    source_rights_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    source_attribution: Mapped[str | None] = mapped_column(Text)
    privacy: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="workspace")
    campaign_id: Mapped[str | None] = mapped_column(String(36))
    keyword_id: Mapped[str | None] = mapped_column(String(36))
    skill_name: Mapped[str | None] = mapped_column(String(128))
    reference_roles_json: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["layout", "font_mood", "overlap"])
    influence: Mapped[str] = mapped_column(String(16), nullable=False, default="balanced")
    preference: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tags_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    analysis_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    editorial_status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate")
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RrugcTypographyStyleDnaModel(Base):
    """One immutable analyzer result per version and source hash, never source text."""
    __tablename__ = "rrugc_typography_style_dna"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "reference_id"],
            ["rrugc_typography_references.tenant_id", "rrugc_typography_references.id"],
            name="fk_typography_dna_reference_tenant",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "reference_id", "analyzer_version", "image_hash",
            name="uq_typography_dna_analysis",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_typography_dna_confidence"),
        Index("ix_typography_dna_style_family", "tenant_id", "style_family"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="typography-dna/v1")
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    image_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    typography_region_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    style_family: Mapped[str] = mapped_column(String(80), nullable=False)
    font_mood_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    layout_graph_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    overlap_rules_json: Mapped[list] = mapped_column(JSON, nullable=False)
    color_description: Mapped[str | None] = mapped_column(String(250))
    embroidery_risks_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    distinctiveness_features_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    perceptual_hash: Mapped[str | None] = mapped_column(String(64))
    embedding_key: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    manual_override_json: Mapped[dict | None] = mapped_column(JSON)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

class RrugcTypographyAnalysisJobModel(Base):
    """Durable, tenant-scoped G3 analysis work. No worker is registered by this model."""

    __tablename__ = "rrugc_typography_analysis_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "reference_id"],
            ["rrugc_typography_references.tenant_id", "rrugc_typography_references.id"],
            name="fk_typography_analysis_job_reference_tenant",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "reference_id", "analyzer_version", "image_hash",
            name="uq_typography_analysis_job_version",
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','needs_review')",
            name="ck_typography_analysis_job_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3",
            name="ck_typography_analysis_job_attempts",
        ),
        Index(
            "ix_typography_analysis_job_claim",
            "tenant_id", "status", "next_attempt_at", "created_at",
        ),
        Index(
            "ix_typography_analysis_job_lease",
            "tenant_id", "status", "lease_expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    image_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
