from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.creative_pipeline.constants import (
    ASPECT_RATIOS, ArtifactType, CreativePlatform, GenerationRunStatus,
    ListingTaskStatus, NodeRunStatus, NodeType, PipelineRunStatus,
    PipelineTriggerType,
)


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _values(enum_type: type) -> str:
    return ",".join(repr(item.value) for item in enum_type)


class SourceGroupModel(Base):
    __tablename__ = "creative_pipeline_source_groups"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "external_source_id"], ["external_sources.tenant_id", "external_sources.id"], ondelete="RESTRICT", name="fk_cp_source_groups_tenant_source"),
        UniqueConstraint("tenant_id", "id", name="uq_cp_source_groups_tenant_id"),
        UniqueConstraint("tenant_id", "external_source_id", "external_folder_id", name="uq_cp_source_groups_provider_folder"),
        UniqueConstraint("tenant_id", "external_source_id", "source_path", name="uq_cp_source_groups_source_path"),
        CheckConstraint("platform IN ('etsy', 'amazon')", name="ck_cp_source_groups_platform"),
        CheckConstraint("external_folder_id IS NOT NULL OR source_path IS NOT NULL", name="ck_cp_source_groups_identity"),
        Index("ix_cp_source_groups_tenant_active", "tenant_id", "active", "scan_enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    external_folder_id: Mapped[str | None] = mapped_column(String(2048))
    source_path: Mapped[str | None] = mapped_column(String(4096))
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    scan_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ListingTaskModel(Base):
    __tablename__ = "creative_pipeline_listing_tasks"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "source_group_id"], ["creative_pipeline_source_groups.tenant_id", "creative_pipeline_source_groups.id"], ondelete="CASCADE", name="fk_cp_listing_tasks_tenant_group"),
        UniqueConstraint("tenant_id", "id", name="uq_cp_listing_tasks_tenant_id"),
        UniqueConstraint("tenant_id", "source_group_id", "external_folder_id", name="uq_cp_listing_tasks_provider_folder"),
        UniqueConstraint("tenant_id", "source_group_id", "folder_path", name="uq_cp_listing_tasks_folder_path"),
        CheckConstraint("status IN ('active', 'missing_source', 'archived')", name="ck_cp_listing_tasks_status"),
        CheckConstraint("external_folder_id IS NOT NULL OR folder_path IS NOT NULL", name="ck_cp_listing_tasks_identity"),
        Index("ix_cp_listing_tasks_tenant_status", "tenant_id", "status", "updated_at"),
        Index("ix_cp_listing_tasks_tenant_key", "tenant_id", "listing_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_group_id: Mapped[str] = mapped_column(String(36), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    listing_key: Mapped[str] = mapped_column(String(512), nullable=False)
    folder_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    folder_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    external_folder_id: Mapped[str | None] = mapped_column(String(2048))
    pipeline_folder_id: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ListingTaskStatus.ACTIVE.value)
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class PipelineRunModel(Base):
    __tablename__ = "creative_pipeline_runs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "listing_task_id"], ["creative_pipeline_listing_tasks.tenant_id", "creative_pipeline_listing_tasks.id"], ondelete="CASCADE", name="fk_cp_runs_tenant_listing"),
        ForeignKeyConstraint(["tenant_id", "parent_run_id"], ["creative_pipeline_runs.tenant_id", "creative_pipeline_runs.id"], ondelete="RESTRICT", name="fk_cp_runs_tenant_parent"),
        UniqueConstraint("tenant_id", "id", name="uq_cp_runs_tenant_id"),
        UniqueConstraint("tenant_id", "listing_task_id", "run_number", name="uq_cp_runs_listing_number"),
        Index("ix_cp_runs_tenant_parent", "tenant_id", "parent_run_id"),
        Index("uq_cp_runs_tenant_listing_idempotency", "tenant_id", "listing_task_id", "operator_idempotency_key", unique=True, postgresql_where=__import__("sqlalchemy").text("operator_idempotency_key IS NOT NULL"), sqlite_where=__import__("sqlalchemy").text("operator_idempotency_key IS NOT NULL")),
        CheckConstraint("run_number > 0", name="ck_cp_runs_run_number"),
        CheckConstraint("status IN ('queued', 'running', 'retrying', 'blocked', 'failed', 'completed', 'cancelled')", name="ck_cp_runs_status"),
        CheckConstraint("trigger_type IN ('discovery', 'manual', 'regenerate')", name="ck_cp_runs_trigger"),
        Index("ix_cp_runs_tenant_status", "tenant_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    listing_task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_run_id: Mapped[str | None] = mapped_column(String(36))
    branch_start_node: Mapped[str | None] = mapped_column(String(48))
    operator_idempotency_key: Mapped[str | None] = mapped_column(String(255))
    run_number: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PipelineRunStatus.QUEUED.value)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default=PipelineTriggerType.DISCOVERY.value)
    triggered_by: Mapped[str | None] = mapped_column(String(255))
    knowledge_snapshot_id: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class NodeRunModel(Base):
    __tablename__ = "creative_pipeline_node_runs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "pipeline_run_id"], ["creative_pipeline_runs.tenant_id", "creative_pipeline_runs.id"], ondelete="CASCADE", name="fk_cp_node_runs_tenant_run"),
        UniqueConstraint("tenant_id", "id", name="uq_cp_node_runs_tenant_id"),
        UniqueConstraint("tenant_id", "pipeline_run_id", "node_type", name="uq_cp_node_runs_logical_node"),
        CheckConstraint("node_type IN ('input_data', 'idea_story', 'prompt', 'video_generation', 'video_output', 'watermark_smart_enhance')", name="ck_cp_node_runs_type"),
        CheckConstraint("status IN ('pending', 'ready', 'running', 'retry_wait', 'completed', 'failed', 'blocked', 'cancelled')", name="ck_cp_node_runs_status"),
        CheckConstraint("attempt_count >= 0", name="ck_cp_node_runs_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_cp_node_runs_max_attempts"),
        Index("ix_cp_node_runs_tenant_status", "tenant_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    node_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=NodeRunStatus.PENDING.value)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=5)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_version: Mapped[str | None] = mapped_column(String(64))
    output_version: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class GenerationRunModel(Base):
    __tablename__ = "creative_pipeline_generation_runs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "pipeline_run_id"], ["creative_pipeline_runs.tenant_id", "creative_pipeline_runs.id"], ondelete="CASCADE", name="fk_cp_generation_runs_tenant_run"),
        ForeignKeyConstraint(["tenant_id", "listing_task_id"], ["creative_pipeline_listing_tasks.tenant_id", "creative_pipeline_listing_tasks.id"], ondelete="CASCADE", name="fk_cp_generation_runs_tenant_listing"),
        UniqueConstraint("tenant_id", "id", name="uq_cp_generation_runs_tenant_id"),
        UniqueConstraint("tenant_id", "listing_task_id", "provider", "model", "aspect_ratio", "generation_number", name="uq_cp_generation_runs_logical_generation"),
        UniqueConstraint("tenant_id", "pipeline_run_id", "provider", "model", "aspect_ratio", "generation_number", name="uq_cp_generation_runs_run_generation"),
        CheckConstraint("aspect_ratio IN ('1:1', '16:9', '9:16')", name="ck_cp_generation_runs_aspect_ratio"),
        CheckConstraint("status IN ('pending', 'submitted', 'running', 'retry_wait', 'completed', 'failed', 'cancelled')", name="ck_cp_generation_runs_status"),
        CheckConstraint("generation_number > 0", name="ck_cp_generation_runs_number"),
        CheckConstraint("attempt_count >= 0", name="ck_cp_generation_runs_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_cp_generation_runs_max_attempts"),
        Index("ix_cp_generation_runs_tenant_status", "tenant_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    listing_task_id: Mapped[str] = mapped_column(String(36), nullable=True)
    prompt_artifact_id: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False)
    generation_number: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=GenerationRunStatus.PENDING.value)
    provider_request_id: Mapped[str | None] = mapped_column(String(512))
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=5)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ArtifactModel(Base):
    __tablename__ = "creative_pipeline_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "listing_task_id"], ["creative_pipeline_listing_tasks.tenant_id", "creative_pipeline_listing_tasks.id"], ondelete="CASCADE", name="fk_cp_artifacts_tenant_listing"),
        ForeignKeyConstraint(["tenant_id", "pipeline_run_id"], ["creative_pipeline_runs.tenant_id", "creative_pipeline_runs.id"], ondelete="CASCADE", name="fk_cp_artifacts_tenant_run"),
        ForeignKeyConstraint(["tenant_id", "node_run_id"], ["creative_pipeline_node_runs.tenant_id", "creative_pipeline_node_runs.id"], ondelete="SET NULL", name="fk_cp_artifacts_tenant_node"),
        ForeignKeyConstraint(["tenant_id", "generation_run_id"], ["creative_pipeline_generation_runs.tenant_id", "creative_pipeline_generation_runs.id"], ondelete="SET NULL", name="fk_cp_artifacts_tenant_generation"),
        UniqueConstraint("tenant_id", "id", name="uq_cp_artifacts_tenant_id"),
        CheckConstraint("artifact_type IN ('input_snapshot', 'input_manifest', 'knowledge_snapshot', 'idea_story', 'prompt', 'generation_metadata', 'raw_video', 'enhanced_video')", name="ck_cp_artifacts_type"),
        CheckConstraint("status IN ('reserved', 'available', 'inconsistent')", name="ck_cp_artifacts_status"),
        Index("uq_cp_artifacts_logical_no_ratio", "tenant_id", "listing_task_id", "artifact_type", "version", unique=True, postgresql_where=__import__("sqlalchemy").text("aspect_ratio IS NULL AND variant_key IS NULL"), sqlite_where=__import__("sqlalchemy").text("aspect_ratio IS NULL AND variant_key IS NULL")),
        Index("uq_cp_artifacts_logical_no_ratio_variant", "tenant_id", "listing_task_id", "artifact_type", "version", "variant_key", unique=True, postgresql_where=__import__("sqlalchemy").text("aspect_ratio IS NULL AND variant_key IS NOT NULL"), sqlite_where=__import__("sqlalchemy").text("aspect_ratio IS NULL AND variant_key IS NOT NULL")),
        Index("uq_cp_artifacts_logical_ratio", "tenant_id", "listing_task_id", "artifact_type", "version", "aspect_ratio", unique=True, postgresql_where=__import__("sqlalchemy").text("aspect_ratio IS NOT NULL AND variant_key IS NULL"), sqlite_where=__import__("sqlalchemy").text("aspect_ratio IS NOT NULL AND variant_key IS NULL")),
        Index("uq_cp_artifacts_logical_ratio_variant", "tenant_id", "listing_task_id", "artifact_type", "version", "aspect_ratio", "variant_key", unique=True, postgresql_where=__import__("sqlalchemy").text("aspect_ratio IS NOT NULL AND variant_key IS NOT NULL"), sqlite_where=__import__("sqlalchemy").text("aspect_ratio IS NOT NULL AND variant_key IS NOT NULL")),
        CheckConstraint("version > 0", name="ck_cp_artifacts_version"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_cp_artifacts_size"),
        CheckConstraint("aspect_ratio IS NULL OR aspect_ratio IN ('1:1', '16:9', '9:16')", name="ck_cp_artifacts_aspect_ratio"),
        Index("ix_cp_artifacts_tenant_type", "tenant_id", "artifact_type", "created_at"),
        Index("ix_cp_artifacts_tenant_hash", "tenant_id", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    listing_task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    node_run_id: Mapped[str | None] = mapped_column(String(36))
    generation_run_id: Mapped[str | None] = mapped_column(String(36))
    artifact_type: Mapped[str] = mapped_column(String(48), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    relative_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    storage_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="source_provider")
    content_hash: Mapped[str | None] = mapped_column(String(128))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    aspect_ratio: Mapped[str | None] = mapped_column(String(16))
    variant_key: Mapped[str | None] = mapped_column(String(128))
    model_provider: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    external_file_id: Mapped[str | None] = mapped_column(String(2048))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)