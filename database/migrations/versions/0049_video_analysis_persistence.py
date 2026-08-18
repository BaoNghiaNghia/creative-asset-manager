"""Persist tenant-safe video analysis profiles, runs, and chunks.

Revision ID: 0049_video_analysis_persistence
Revises: 0048_viewer_delete
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0049_video_analysis_persistence"
down_revision = "0048_viewer_delete"
branch_labels = None
depends_on = None


JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "video_metadata_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("profile_name", sa.String(length=255), nullable=False),
        sa.Column("profile_version", sa.String(length=100), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("optional_json_schema", JSON_DOCUMENT, nullable=True),
        sa.Column("search_config_json", JSON_DOCUMENT, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "profile_name", "profile_version",
            name="uq_video_metadata_profiles_tenant_name_version",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_video_metadata_profiles_tenant_id",
        ),
    )
    op.create_index(
        "ix_video_metadata_profiles_active", "video_metadata_profiles", ["tenant_id", "active"],
    )

    op.create_table(
        "video_analysis_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("source_asset_id", sa.String(length=36), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("video_metadata_profile_id", sa.String(length=36), nullable=False),
        sa.Column("metadata_profile", sa.String(length=255), nullable=False),
        sa.Column("metadata_profile_version", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("analysis_version", sa.String(length=100), nullable=False),
        sa.Column("ai_provider", sa.String(length=100), nullable=True),
        sa.Column("ai_model", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("source_width", sa.Integer(), nullable=True),
        sa.Column("source_height", sa.Integer(), nullable=True),
        sa.Column("chunk_seconds", sa.Integer(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("completed_chunks", sa.Integer(), nullable=False),
        sa.Column("summary_json", JSON_DOCUMENT, nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_asset_id"],
            ["source_assets.tenant_id", "source_assets.id"],
            name="fk_video_analysis_runs_tenant_source_asset", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "video_metadata_profile_id"],
            ["video_metadata_profiles.tenant_id", "video_metadata_profiles.id"],
            name="fk_video_analysis_runs_tenant_profile", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_video_analysis_runs_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_video_analysis_runs_tenant_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('pending','preparing','analyzing','completed','failed','cancelled')",
            name="ck_video_analysis_runs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_video_analysis_runs_attempt_count"),
        sa.CheckConstraint("chunk_seconds > 0", name="ck_video_analysis_runs_chunk_seconds"),
        sa.CheckConstraint("total_chunks >= 0", name="ck_video_analysis_runs_total_chunks"),
        sa.CheckConstraint("completed_chunks >= 0", name="ck_video_analysis_runs_completed_chunks"),
        sa.CheckConstraint("completed_chunks <= total_chunks", name="ck_video_analysis_runs_chunk_progress"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_video_analysis_runs_duration"),
        sa.CheckConstraint("source_width IS NULL OR source_width > 0", name="ck_video_analysis_runs_width"),
        sa.CheckConstraint("source_height IS NULL OR source_height > 0", name="ck_video_analysis_runs_height"),
    )
    op.create_index(
        "ix_video_analysis_runs_source_history", "video_analysis_runs",
        ["tenant_id", "source_asset_id", "created_at"],
    )
    op.create_index(
        "ix_video_analysis_runs_status_created", "video_analysis_runs",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index(
        "ix_video_analysis_runs_fingerprint", "video_analysis_runs",
        ["tenant_id", "source_asset_id", "source_fingerprint"],
    )

    op.create_table(
        "video_analysis_chunks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("source_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("source_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("proxy_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("provider_file_name", sa.String(length=1024), nullable=True),
        sa.Column("provider_file_uri", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSON_DOCUMENT, nullable=True),
        sa.Column("usage_json", JSON_DOCUMENT, nullable=True),
        sa.Column("provider_metadata_json", JSON_DOCUMENT, nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["video_analysis_runs.tenant_id", "video_analysis_runs.id"],
            name="fk_video_analysis_chunks_tenant_run", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id", "chunk_index", name="uq_video_analysis_chunks_run_index",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_video_analysis_chunks_tenant_id",
        ),
        sa.CheckConstraint(
            "status IN ('pending','preparing','uploaded','analyzing','completed','failed')",
            name="ck_video_analysis_chunks_status",
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_video_analysis_chunks_index"),
        sa.CheckConstraint("source_start_ms >= 0", name="ck_video_analysis_chunks_start"),
        sa.CheckConstraint("source_end_ms > source_start_ms", name="ck_video_analysis_chunks_range"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_video_analysis_chunks_attempt_count"),
        sa.CheckConstraint(
            "proxy_size_bytes IS NULL OR proxy_size_bytes >= 0",
            name="ck_video_analysis_chunks_proxy_size",
        ),
    )
    op.create_index(
        "ix_video_analysis_chunks_run", "video_analysis_chunks",
        ["tenant_id", "run_id", "chunk_index"],
    )
    op.create_index(
        "ix_video_analysis_chunks_status", "video_analysis_chunks",
        ["tenant_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_analysis_chunks_status", table_name="video_analysis_chunks")
    op.drop_index("ix_video_analysis_chunks_run", table_name="video_analysis_chunks")
    op.drop_table("video_analysis_chunks")
    op.drop_index("ix_video_analysis_runs_fingerprint", table_name="video_analysis_runs")
    op.drop_index("ix_video_analysis_runs_status_created", table_name="video_analysis_runs")
    op.drop_index("ix_video_analysis_runs_source_history", table_name="video_analysis_runs")
    op.drop_table("video_analysis_runs")
    op.drop_index("ix_video_metadata_profiles_active", table_name="video_metadata_profiles")
    op.drop_table("video_metadata_profiles")
