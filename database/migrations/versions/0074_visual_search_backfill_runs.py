"""add durable visual search backfill runs.

Revision ID: 0074_visual_backfill_runs
Revises: 0073_same_day_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = "0074_visual_backfill_runs"
down_revision = "0073_same_day_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "visual_search_backfill_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("schema_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("checkpoint_asset_id", sa.String(36)),
        sa.Column("counters_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("requested_by", sa.String(255)),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'running', 'paused', 'completed', 'failed', 'cancelled')", name="ck_visual_backfill_status"),
    )
    op.create_index("ix_visual_backfill_tenant_updated", "visual_search_backfill_runs", ["tenant_id", "updated_at"])
    op.create_index("ix_visual_backfill_tenant_status", "visual_search_backfill_runs", ["tenant_id", "status"])


def downgrade():
    op.drop_index("ix_visual_backfill_tenant_status", table_name="visual_search_backfill_runs")
    op.drop_index("ix_visual_backfill_tenant_updated", table_name="visual_search_backfill_runs")
    op.drop_table("visual_search_backfill_runs")
