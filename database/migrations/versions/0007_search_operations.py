"""Create resumable search maintenance operation state.

Revision ID: 0007_search_operations
Revises: 0006_metadata_sidecars

Rollback: stop operational commands, allow active runs to finish or cancel them,
export run audit data if required, then downgrade. PostgreSQL projections and
Elasticsearch indices/aliases are not modified by downgrade.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_search_operations"
down_revision = "0006_metadata_sidecars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_operation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("operation_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("target_projection_version", sa.String(100), nullable=False),
        sa.Column("target_index", sa.String(255)),
        sa.Column("alias_switch_json", sa.JSON()),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False),
        sa.Column("cursor_created_at", sa.DateTime(timezone=True)),
        sa.Column("cursor_analysis_id", sa.String(36)),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_search_operation_runs_tenant_id",
        ),
        sa.CheckConstraint(
            "operation_type IN ('rebuild_projections', 'reindex_assets', 'rebuild_and_reindex')",
            name="ck_search_operation_runs_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_search_operation_runs_status",
        ),
        sa.CheckConstraint(
            "page_size > 0 AND page_size <= 500",
            name="ck_search_operation_runs_page",
        ),
    )
    op.create_index(
        "ix_search_operation_runs_tenant_status",
        "search_operation_runs",
        ["tenant_id", "status", "created_at"],
    )
    op.create_table(
        "search_operation_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["search_operation_runs.tenant_id", "search_operation_runs.id"],
            ondelete="CASCADE",
            name="fk_search_operation_items_run",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["asset_ai_analyses.id"],
            ondelete="CASCADE",
            name="fk_search_operation_items_analysis",
        ),
        sa.UniqueConstraint(
            "run_id",
            "analysis_id",
            name="uq_search_operation_items_analysis",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'skipped')",
            name="ck_search_operation_items_status",
        ),
    )
    op.create_index(
        "ix_search_operation_items_run_status",
        "search_operation_items",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_operation_items_run_status", table_name="search_operation_items")
    op.drop_table("search_operation_items")
    op.drop_index("ix_search_operation_runs_tenant_status", table_name="search_operation_runs")
    op.drop_table("search_operation_runs")
