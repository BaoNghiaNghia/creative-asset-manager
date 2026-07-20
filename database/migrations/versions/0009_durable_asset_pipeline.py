"""Add durable asset pipeline state and correlation records.

Revision ID: 0009_durable_asset_pipeline
Revises: 0008_ai_single_analysis

Rollback: drain processing workers first. Downgrade removes operator-visible
pipeline state only; assets, analyses, storage records and jobs are preserved.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_durable_asset_pipeline"
down_revision = "0008_ai_single_analysis"
branch_labels = None
depends_on = None

STATES = (
    "discovered", "download_pending", "downloading", "downloaded", "duplicate_detected",
    "storage_pending", "stored", "analysis_pending", "analyzing", "metadata_ready",
    "projection_pending", "projection_ready", "search_pending", "indexed",
    "sidecar_pending", "completed", "download_failed", "storage_failed",
    "analysis_failed", "projection_failed", "search_failed", "sidecar_failed",
)


def upgrade() -> None:
    op.create_table(
        "asset_pipelines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("origin_type", sa.String(32), nullable=False),
        sa.Column("origin_id", sa.String(64), nullable=False),
        sa.Column("source_asset_id", sa.String(36)),
        sa.Column("asset_id", sa.String(36)),
        sa.Column("analysis_id", sa.String(36)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("projection_version", sa.String(64)),
        sa.Column("projection_checksum", sa.String(64)),
        sa.Column("indexed_projection_version", sa.String(64)),
        sa.Column("indexed_projection_checksum", sa.String(64)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("failure_retryable", sa.Boolean()),
        sa.Column("status_data_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id", "source_asset_id"], ["source_assets.tenant_id", "source_assets.id"], ondelete="SET NULL", name="fk_asset_pipelines_source_asset"),
        sa.ForeignKeyConstraint(["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"], ondelete="SET NULL", name="fk_asset_pipelines_asset"),
        sa.UniqueConstraint("tenant_id", "correlation_id", name="uq_asset_pipelines_tenant_correlation"),
        sa.UniqueConstraint("tenant_id", "origin_type", "origin_id", name="uq_asset_pipelines_tenant_origin"),
        sa.CheckConstraint("origin_type IN ('source_asset', 'ingestion_item')", name="ck_asset_pipelines_origin_type"),
        sa.CheckConstraint("state IN (%s)" % ",".join(repr(state) for state in STATES), name="ck_asset_pipelines_state"),
    )
    op.create_index("ix_asset_pipelines_status", "asset_pipelines", ["tenant_id", "state", "updated_at"])
    op.create_index("ix_asset_pipelines_asset", "asset_pipelines", ["tenant_id", "asset_id"])


def downgrade() -> None:
    op.drop_index("ix_asset_pipelines_asset", table_name="asset_pipelines")
    op.drop_index("ix_asset_pipelines_status", table_name="asset_pipelines")
    op.drop_table("asset_pipelines")
