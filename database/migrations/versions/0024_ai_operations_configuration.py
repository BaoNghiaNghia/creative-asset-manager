"""Add tenant AI Operations configuration fields.

Revision ID: 0024_ai_operations_configuration
Revises: 0023_ai_operations_controls

Rollback removes only tenant UI configuration. Provider, budget and audit data
remain authoritative and are preserved.
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_ai_operations_configuration"
down_revision = "0023_ai_operations_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_processing_policies", sa.Column("default_ai_mode", sa.String(length=16), server_default="single", nullable=False))
    op.add_column("tenant_processing_policies", sa.Column("default_metadata_profile", sa.String(length=255), nullable=True))
    op.add_column("tenant_processing_policies", sa.Column("auto_analyze_new_assets", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("tenant_processing_policies", sa.Column("daily_ai_item_limit", sa.Integer(), server_default="100", nullable=False))
    op.add_column("tenant_processing_policies", sa.Column("ai_retry_count", sa.Integer(), server_default="2", nullable=False))
    op.add_column("tenant_processing_policies", sa.Column("ai_timeout_seconds", sa.Integer(), server_default="60", nullable=False))
    op.create_check_constraint("ck_tenant_policy_default_ai_mode", "tenant_processing_policies", "default_ai_mode IN ('single', 'batch')")
    op.create_check_constraint("ck_tenant_policy_ai_ops_limits", "tenant_processing_policies", "daily_ai_item_limit > 0 AND ai_retry_count >= 0 AND ai_timeout_seconds > 0")


def downgrade() -> None:
    op.drop_constraint("ck_tenant_policy_ai_ops_limits", "tenant_processing_policies", type_="check")
    op.drop_constraint("ck_tenant_policy_default_ai_mode", "tenant_processing_policies", type_="check")
    op.drop_column("tenant_processing_policies", "ai_timeout_seconds")
    op.drop_column("tenant_processing_policies", "ai_retry_count")
    op.drop_column("tenant_processing_policies", "daily_ai_item_limit")
    op.drop_column("tenant_processing_policies", "auto_analyze_new_assets")
    op.drop_column("tenant_processing_policies", "default_metadata_profile")
    op.drop_column("tenant_processing_policies", "default_ai_mode")