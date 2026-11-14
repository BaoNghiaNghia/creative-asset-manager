"""Phase 4C.3A: inert PostgreSQL executor-slot lease/fencing ledger.

No producer/consumer is wired to this table. Upgrade creates an EMPTY table;
neither jobs nor idle observations are imported or dispatched automatically.
Revision ID: 0209_cam_agent_executor_slots
Revises: 0208_etsy_spy_agent_review_receipts
"""
from alembic import op
import sqlalchemy as sa


revision = "0209_cam_agent_executor_slots"
down_revision = "0208_etsy_spy_agent_review_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_agent_executor_slots",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column(
            "fencing_token", sa.BigInteger(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "agent_id"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["etsy_spy_agents.id"],
            name="fk_cam_executor_slot_agent", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["processing_jobs.id"],
            name="fk_cam_executor_slot_job", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "job_id", name="uq_cam_executor_slot_tenant_job",
        ),
        sa.CheckConstraint(
            "fencing_token >= 0", name="ck_cam_executor_slot_fence",
        ),
        sa.CheckConstraint(
            "(job_id IS NULL AND lease_expires_at IS NULL) OR "
            "(job_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_cam_executor_slot_pair",
        ),
    )
    op.create_index(
        "ix_cam_executor_slot_expiry", "cam_agent_executor_slots",
        ["lease_expires_at"],
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text(
        "SELECT COUNT(*) FROM cam_agent_executor_slots"
    )):
        raise RuntimeError(
            "Executor slot fencing history must survive code-only rollback; "
            "populated 0209 downgrade refused"
        )
    op.drop_index(
        "ix_cam_executor_slot_expiry", table_name="cam_agent_executor_slots",
    )
    op.drop_table("cam_agent_executor_slots")
