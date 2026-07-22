"""Add tenant AI Operations configuration fields.

Revision ID: 0024_ai_operations_configuration
Revises: 0023_ai_operations_controls

Rollback removes only tenant UI configuration. Provider, budget and audit data
remain authoritative and are preserved. Batch operations keep the migration
portable for the SQLite development convenience path while emitting normal
ALTER statements on PostgreSQL.
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_ai_operations_configuration"
down_revision = "0023_ai_operations_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_processing_policies") as batch_op:
        batch_op.add_column(sa.Column("default_ai_mode", sa.String(length=16), server_default="single", nullable=False))
        batch_op.add_column(sa.Column("default_metadata_profile", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("auto_analyze_new_assets", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column("daily_ai_item_limit", sa.Integer(), server_default="100", nullable=False))
        batch_op.add_column(sa.Column("ai_retry_count", sa.Integer(), server_default="2", nullable=False))
        batch_op.add_column(sa.Column("ai_timeout_seconds", sa.Integer(), server_default="60", nullable=False))
        batch_op.create_check_constraint("ck_tenant_policy_default_ai_mode", "default_ai_mode IN ('single', 'batch')")
        batch_op.create_check_constraint("ck_tenant_policy_ai_ops_limits", "daily_ai_item_limit > 0 AND ai_retry_count >= 0 AND ai_timeout_seconds > 0")


def downgrade() -> None:
    with op.batch_alter_table("tenant_processing_policies") as batch_op:
        batch_op.drop_constraint("ck_tenant_policy_ai_ops_limits", type_="check")
        batch_op.drop_constraint("ck_tenant_policy_default_ai_mode", type_="check")
        batch_op.drop_column("ai_timeout_seconds")
        batch_op.drop_column("ai_retry_count")
        batch_op.drop_column("daily_ai_item_limit")
        batch_op.drop_column("auto_analyze_new_assets")
        batch_op.drop_column("default_metadata_profile")
        batch_op.drop_column("default_ai_mode")
