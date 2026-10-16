"""BD-01 staged scan tasks and lease fencing metadata, scanner remains disabled.

Revision ID: 0160_branding_dna_scan_tasks
Revises: 0159_branding_dna_evidence
"""
from alembic import op
import sqlalchemy as sa

revision = "0160_branding_dna_scan_tasks"
down_revision = "0159_branding_dna_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bc_scan_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("task_kind", sa.String(40), nullable=False, server_default="verify_listing_evidence"),
        sa.Column("status", sa.String(20), nullable=False, server_default="staged"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_id", sa.String(36)),
        sa.Column("agent_id", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("retry_after", sa.DateTime(timezone=True)),
        sa.Column("checkpoint_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(40)),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id", "snapshot_id"],
            ["bc_listing_snapshots.tenant_id", "bc_listing_snapshots.shop_id", "bc_listing_snapshots.id"],
            name="fk_bc_scan_task_snapshot_scope", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "shop_id", "snapshot_id", "task_kind",
                            name="uq_bc_scan_task_idempotency"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= max_attempts AND max_attempts BETWEEN 1 AND 5",
                           name="ck_bc_scan_task_attempts"),
        sa.CheckConstraint("lease_generation >= 0 AND checkpoint_seq >= 0",
                           name="ck_bc_scan_task_fencing"),
        sa.CheckConstraint(
            "status IN ('staged','pending','leased','retry','blocked','completed','cancelled')",
            name="ck_bc_scan_task_status",
        ),
    )
    op.create_index("ix_bc_scan_task_queue", "bc_scan_tasks",
                    ["tenant_id", "status", "retry_after", "created_at"])
    op.create_index("ix_bc_scan_task_shop", "bc_scan_tasks",
                    ["tenant_id", "shop_id", "created_at"])


def downgrade() -> None:
    # Destructive downgrade requires an explicit retention review; normal
    # rollback disables new task creation and preserves the stored queue.
    op.drop_index("ix_bc_scan_task_shop", table_name="bc_scan_tasks")
    op.drop_index("ix_bc_scan_task_queue", table_name="bc_scan_tasks")
    op.drop_table("bc_scan_tasks")
