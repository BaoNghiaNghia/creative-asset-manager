"""add Creative Pipeline CP-01 domain tables.

Revision ID: 0075_creative_pipeline_domain
Revises: 0074_visual_backfill_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0075_creative_pipeline_domain"
down_revision = "0074_visual_backfill_runs"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    # Alembic's legacy version table is VARCHAR(32), but the CP revisions use
    # descriptive identifiers longer than 32 characters. Widen it before this
    # migration completes so Alembic can record the next revision atomically.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.create_table(
        "creative_pipeline_source_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_provider", sa.String(64), nullable=False),
        sa.Column("external_source_id", sa.String(36), nullable=False),
        sa.Column("external_folder_id", sa.String(2048)),
        sa.Column("source_path", sa.String(4096)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scan_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.Column("last_scan_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_scan_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id", "external_source_id"], ["external_sources.tenant_id", "external_sources.id"], ondelete="RESTRICT", name="fk_cp_source_groups_tenant_source"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cp_source_groups_tenant_id"),
        sa.UniqueConstraint("tenant_id", "external_source_id", "external_folder_id", name="uq_cp_source_groups_provider_folder"),
        sa.UniqueConstraint("tenant_id", "external_source_id", "source_path", name="uq_cp_source_groups_source_path"),
        sa.CheckConstraint("platform IN ('etsy', 'amazon')", name="ck_cp_source_groups_platform"),
        sa.CheckConstraint("external_folder_id IS NOT NULL OR source_path IS NOT NULL", name="ck_cp_source_groups_identity"),
    )
    op.create_index("ix_cp_source_groups_tenant_active", "creative_pipeline_source_groups", ["tenant_id", "active", "scan_enabled"])

    op.create_table(
        "creative_pipeline_listing_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("source_group_id", sa.String(36), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("listing_key", sa.String(512), nullable=False),
        sa.Column("folder_name", sa.String(1024), nullable=False),
        sa.Column("folder_path", sa.String(4096), nullable=False),
        sa.Column("external_folder_id", sa.String(2048)),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id", "source_group_id"], ["creative_pipeline_source_groups.tenant_id", "creative_pipeline_source_groups.id"], ondelete="CASCADE", name="fk_cp_listing_tasks_tenant_group"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cp_listing_tasks_tenant_id"),
        sa.UniqueConstraint("tenant_id", "source_group_id", "external_folder_id", name="uq_cp_listing_tasks_provider_folder"),
        sa.UniqueConstraint("tenant_id", "source_group_id", "folder_path", name="uq_cp_listing_tasks_folder_path"),
        sa.CheckConstraint("status IN ('active', 'missing_source', 'archived')", name="ck_cp_listing_tasks_status"),
        sa.CheckConstraint("external_folder_id IS NOT NULL OR folder_path IS NOT NULL", name="ck_cp_listing_tasks_identity"),
    )
    op.create_index("ix_cp_listing_tasks_tenant_status", "creative_pipeline_listing_tasks", ["tenant_id", "status", "updated_at"])
    op.create_index("ix_cp_listing_tasks_tenant_key", "creative_pipeline_listing_tasks", ["tenant_id", "listing_key"])

    op.create_table(
        "creative_pipeline_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("listing_task_id", sa.String(36), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("trigger_type", sa.String(32), nullable=False, server_default="discovery"),
        sa.Column("triggered_by", sa.String(255)),
        sa.Column("knowledge_snapshot_id", sa.String(255)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id", "listing_task_id"], ["creative_pipeline_listing_tasks.tenant_id", "creative_pipeline_listing_tasks.id"], ondelete="CASCADE", name="fk_cp_runs_tenant_listing"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cp_runs_tenant_id"),
        sa.UniqueConstraint("tenant_id", "listing_task_id", "run_number", name="uq_cp_runs_listing_number"),
        sa.CheckConstraint("run_number > 0", name="ck_cp_runs_run_number"),
        sa.CheckConstraint("status IN ('queued', 'running', 'retrying', 'blocked', 'failed', 'completed', 'cancelled')", name="ck_cp_runs_status"),
        sa.CheckConstraint("trigger_type IN ('discovery', 'manual', 'regenerate')", name="ck_cp_runs_trigger"),
    )
    op.create_index("ix_cp_runs_tenant_status", "creative_pipeline_runs", ["tenant_id", "status", "updated_at"])

    op.create_table(
        "creative_pipeline_node_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("pipeline_run_id", sa.String(36), nullable=False),
        sa.Column("node_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("input_version", sa.String(64)),
        sa.Column("output_version", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id", "pipeline_run_id"], ["creative_pipeline_runs.tenant_id", "creative_pipeline_runs.id"], ondelete="CASCADE", name="fk_cp_node_runs_tenant_run"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cp_node_runs_tenant_id"),
        sa.UniqueConstraint("tenant_id", "pipeline_run_id", "node_type", name="uq_cp_node_runs_logical_node"),
        sa.CheckConstraint("node_type IN ('input_data', 'idea_story', 'prompt', 'video_generation', 'video_output', 'watermark_smart_enhance')", name="ck_cp_node_runs_type"),
        sa.CheckConstraint("status IN ('pending', 'ready', 'running', 'retry_wait', 'completed', 'failed', 'blocked', 'cancelled')", name="ck_cp_node_runs_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_cp_node_runs_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_cp_node_runs_max_attempts"),
    )
    op.create_index("ix_cp_node_runs_tenant_status", "creative_pipeline_node_runs", ["tenant_id", "status", "updated_at"])

    op.create_table(
        "creative_pipeline_generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("pipeline_run_id", sa.String(36), nullable=False),
        sa.Column("prompt_artifact_id", sa.String(36)),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("aspect_ratio", sa.String(16), nullable=False),
        sa.Column("generation_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("provider_request_id", sa.String(512)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id", "pipeline_run_id"], ["creative_pipeline_runs.tenant_id", "creative_pipeline_runs.id"], ondelete="CASCADE", name="fk_cp_generation_runs_tenant_run"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cp_generation_runs_tenant_id"),
        sa.UniqueConstraint("tenant_id", "pipeline_run_id", "provider", "model", "aspect_ratio", "generation_number", name="uq_cp_generation_runs_logical_generation"),
        sa.CheckConstraint("aspect_ratio IN ('1:1', '16:9', '9:16')", name="ck_cp_generation_runs_aspect_ratio"),
        sa.CheckConstraint("status IN ('pending', 'submitted', 'running', 'retry_wait', 'completed', 'failed', 'cancelled')", name="ck_cp_generation_runs_status"),
        sa.CheckConstraint("generation_number > 0", name="ck_cp_generation_runs_number"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_cp_generation_runs_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_cp_generation_runs_max_attempts"),
    )
    op.create_index("ix_cp_generation_runs_tenant_status", "creative_pipeline_generation_runs", ["tenant_id", "status", "updated_at"])

    op.create_table(
        "creative_pipeline_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("listing_task_id", sa.String(36), nullable=False),
        sa.Column("pipeline_run_id", sa.String(36), nullable=False),
        sa.Column("node_run_id", sa.String(36)),
        sa.Column("generation_run_id", sa.String(36)),
        sa.Column("artifact_type", sa.String(48), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(4096), nullable=False),
        sa.Column("storage_kind", sa.String(32), nullable=False, server_default="source_provider"),
        sa.Column("content_hash", sa.String(128)),
        sa.Column("mime_type", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("aspect_ratio", sa.String(16)),
        sa.Column("model_provider", sa.String(64)),
        sa.Column("model_name", sa.String(128)),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "listing_task_id"], ["creative_pipeline_listing_tasks.tenant_id", "creative_pipeline_listing_tasks.id"], ondelete="CASCADE", name="fk_cp_artifacts_tenant_listing"),
        sa.ForeignKeyConstraint(["tenant_id", "pipeline_run_id"], ["creative_pipeline_runs.tenant_id", "creative_pipeline_runs.id"], ondelete="CASCADE", name="fk_cp_artifacts_tenant_run"),
        sa.ForeignKeyConstraint(["tenant_id", "node_run_id"], ["creative_pipeline_node_runs.tenant_id", "creative_pipeline_node_runs.id"], ondelete="SET NULL", name="fk_cp_artifacts_tenant_node"),
        sa.ForeignKeyConstraint(["tenant_id", "generation_run_id"], ["creative_pipeline_generation_runs.tenant_id", "creative_pipeline_generation_runs.id"], ondelete="SET NULL", name="fk_cp_artifacts_tenant_generation"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cp_artifacts_tenant_id"),
        sa.UniqueConstraint("tenant_id", "pipeline_run_id", "artifact_type", "version", "aspect_ratio", name="uq_cp_artifacts_logical_version"),
        sa.CheckConstraint("artifact_type IN ('input_snapshot', 'input_manifest', 'idea_story', 'prompt', 'generation_metadata', 'raw_video', 'enhanced_video')", name="ck_cp_artifacts_type"),
        sa.CheckConstraint("version > 0", name="ck_cp_artifacts_version"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_cp_artifacts_size"),
        sa.CheckConstraint("aspect_ratio IS NULL OR aspect_ratio IN ('1:1', '16:9', '9:16')", name="ck_cp_artifacts_aspect_ratio"),
    )
    op.create_index("ix_cp_artifacts_tenant_type", "creative_pipeline_artifacts", ["tenant_id", "artifact_type", "created_at"])
    op.create_index("ix_cp_artifacts_tenant_hash", "creative_pipeline_artifacts", ["tenant_id", "content_hash"])


def downgrade():
    op.drop_index("ix_cp_artifacts_tenant_hash", table_name="creative_pipeline_artifacts")
    op.drop_index("ix_cp_artifacts_tenant_type", table_name="creative_pipeline_artifacts")
    op.drop_table("creative_pipeline_artifacts")
    op.drop_index("ix_cp_generation_runs_tenant_status", table_name="creative_pipeline_generation_runs")
    op.drop_table("creative_pipeline_generation_runs")
    op.drop_index("ix_cp_node_runs_tenant_status", table_name="creative_pipeline_node_runs")
    op.drop_table("creative_pipeline_node_runs")
    op.drop_index("ix_cp_runs_tenant_status", table_name="creative_pipeline_runs")
    op.drop_table("creative_pipeline_runs")
    op.drop_index("ix_cp_listing_tasks_tenant_key", table_name="creative_pipeline_listing_tasks")
    op.drop_index("ix_cp_listing_tasks_tenant_status", table_name="creative_pipeline_listing_tasks")
    op.drop_table("creative_pipeline_listing_tasks")
    op.drop_index("ix_cp_source_groups_tenant_active", table_name="creative_pipeline_source_groups")
    op.drop_table("creative_pipeline_source_groups")