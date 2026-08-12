"""Add Inventory daily finalization audit events.

Revision ID: 0039_inventory_daily_run
Revises: 0038_inventory_txn
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_inventory_daily_run"
down_revision = "0038_inventory_txn"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_daily_run_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("daily_run_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "daily_run_id"],
            ["inventory_daily_runs.tenant_id", "inventory_daily_runs.id"],
            ondelete="CASCADE",
            name="fk_inventory_daily_run_events_tenant_run",
        ),
        sa.CheckConstraint(
            "event_type IN ('completeness_check','preclose_check','finalized','forced_finalized')",
            name="ck_inventory_daily_run_events_type",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inventory_daily_run_events_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "daily_run_id", "idempotency_key",
            name="uq_inventory_daily_run_events_tenant_key",
        ),
    )
    op.create_index(
        "ix_inventory_daily_run_events_run",
        "inventory_daily_run_events",
        ["tenant_id", "daily_run_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_inventory_daily_run_events_run", table_name="inventory_daily_run_events")
    op.drop_table("inventory_daily_run_events")
