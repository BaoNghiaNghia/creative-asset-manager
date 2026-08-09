"""Add isolated Inventory processing controls and job queue.

Revision ID: 0033_inventory_isolation
Revises: 0032_viewer_folder_scopes
"""

from alembic import op
import sqlalchemy as sa


revision = "0033_inventory_isolation"
down_revision = "0032_viewer_folder_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_processing_controls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("paused", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("max_active_jobs", sa.Integer(), server_default="1", nullable=False),
        sa.Column("max_ai_jobs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("max_active_jobs > 0", name="ck_inventory_controls_max_active"),
        sa.CheckConstraint("max_ai_jobs >= 0", name="ck_inventory_controls_max_ai"),
        sa.CheckConstraint("max_ai_jobs <= max_active_jobs", name="ck_inventory_controls_ai_within_active"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_inventory_controls_tenant"),
    )
    op.create_table(
        "inventory_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_by", sa.String(length=255), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("priority >= 0", name="ck_inventory_jobs_priority"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_inventory_jobs_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_inventory_jobs_max_attempts"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'retry', 'completed', 'failed')", name="ck_inventory_jobs_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_inventory_jobs_tenant_key"),
    )
    op.create_index("ix_inventory_jobs_available", "inventory_jobs", ["status", "next_attempt_at", "priority", "created_at"])
    op.create_index("ix_inventory_jobs_lease", "inventory_jobs", ["status", "lease_expires_at"])
    op.create_index("ix_inventory_jobs_tenant_status", "inventory_jobs", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_inventory_jobs_tenant_status", table_name="inventory_jobs")
    op.drop_index("ix_inventory_jobs_lease", table_name="inventory_jobs")
    op.drop_index("ix_inventory_jobs_available", table_name="inventory_jobs")
    op.drop_table("inventory_jobs")
    op.drop_table("inventory_processing_controls")
