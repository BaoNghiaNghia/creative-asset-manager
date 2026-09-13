"""persist authoritative same-day Gemini reconciliation state.

Revision ID: 0073_inventory_same_day_lifecycle
Revises: 0072_prompt_snapshot_freeze
"""
from alembic import op
import sqlalchemy as sa

revision = "0073_same_day_lifecycle"
down_revision = "0072_prompt_snapshot_freeze"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_reconcile_status", sa.String(32), nullable=False, server_default="pending"))
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_reconcile_run_id", sa.String(64)))
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_reconcile_plan_hash", sa.String(64)))
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_reconcile_started_at", sa.DateTime(timezone=True)))
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_reconcile_completed_at", sa.DateTime(timezone=True)))
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_reconcile_verified_at", sa.DateTime(timezone=True)))
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_reconcile_error_code", sa.String(100)))
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_reconcile_error_message", sa.Text()))


def downgrade():
    for name in ("gemini_reconcile_error_message", "gemini_reconcile_error_code", "gemini_reconcile_verified_at", "gemini_reconcile_completed_at", "gemini_reconcile_started_at", "gemini_reconcile_plan_hash", "gemini_reconcile_run_id", "gemini_reconcile_status"):
        op.drop_column("inventory_daily_sheet_snapshots", name)
