"""add trusted shared-workbook daily carry-forward

Revision ID: 0070_inventory_daily_carry_forward
Revises: 0069_inventory_daily_gemini_copy
"""

from alembic import op
import sqlalchemy as sa

revision = "0070_inventory_daily_carry_forward"
down_revision = "0069_inventory_daily_gemini_copy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_settings", sa.Column("daily_carry_forward_time_local", sa.String(length=5), nullable=False, server_default="09:00"))
    op.create_table(
        "inventory_daily_carry_forwards",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("target_business_date", sa.Date(), nullable=False),
        sa.Column("previous_business_date", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(length=36)),
        sa.Column("source_gemini_file_id", sa.String(length=2048)),
        sa.Column("shared_target_file_id", sa.String(length=2048)),
        sa.Column("warehouse_sheet_identity_json", sa.JSON(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("plan_hash", sa.String(length=64)),
        sa.Column("material_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warehouse_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issue_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=100)), sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "previous_snapshot_id"], ["inventory_daily_sheet_snapshots.tenant_id", "inventory_daily_sheet_snapshots.id"], ondelete="RESTRICT", name="fk_inventory_carry_forward_previous_snapshot"),
        sa.UniqueConstraint("tenant_id", "target_business_date", name="uq_inventory_carry_forward_tenant_date"),
        sa.CheckConstraint("status IN ('pending','planning','applying','verifying','completed','review_required','retryable_failure','terminal_failure')", name="ck_inventory_carry_forward_status"),
    )
    op.create_index("ix_inventory_carry_forward_status", "inventory_daily_carry_forwards", ["tenant_id", "status", "target_business_date"])


def downgrade() -> None:
    op.drop_index("ix_inventory_carry_forward_status", table_name="inventory_daily_carry_forwards")
    op.drop_table("inventory_daily_carry_forwards")
    op.drop_column("inventory_settings", "daily_carry_forward_time_local")
