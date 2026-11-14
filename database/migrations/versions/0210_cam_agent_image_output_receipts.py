"""Phase 4C.3D: inert fenced PNG result receipt ledger.

Adds an EMPTY table only, with no historical import or runtime caller.
Production execution requires independent restore evidence and a reviewed
migration release; a populated ledger may never be destructively downgraded.

Revision ID: 0210_cam_agent_image_output_receipts
Revises: 0209_cam_agent_executor_slots
"""
from alembic import op
import sqlalchemy as sa

revision = "0210_cam_agent_image_output_receipts"
down_revision = "0209_cam_agent_executor_slots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_agent_image_output_receipts",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("claim_attempt_count", sa.Integer(), nullable=False),
        sa.Column("claim_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("receipt_id", sa.String(64), nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("output_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False,
            server_default=sa.text("'prepared'"),
        ),
        sa.Column(
            "prepared_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "job_id"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["processing_jobs.id"],
            name="fk_cam_agent_output_receipt_job", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["etsy_spy_agents.id"],
            name="fk_cam_agent_output_receipt_agent", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "receipt_id", name="uq_cam_agent_output_receipt_id",
        ),
        sa.CheckConstraint(
            "claim_attempt_count > 0 AND fencing_token > 0",
            name="ck_cam_agent_output_receipt_epoch",
        ),
        sa.CheckConstraint(
            "output_bytes > 0 AND output_bytes <= 22000000",
            name="ck_cam_agent_output_receipt_size",
        ),
        sa.CheckConstraint(
            "length(output_sha256) = 64 AND length(source_sha256) = 64",
            name="ck_cam_agent_output_receipt_digest",
        ),
        sa.CheckConstraint(
            "engine IN ('chatgpt_assisted', 'fast_cpu')",
            name="ck_cam_agent_output_receipt_engine",
        ),
        sa.CheckConstraint(
            "(status = 'prepared' AND accepted_at IS NULL) OR "
            "(status = 'accepted' AND accepted_at IS NOT NULL)",
            name="ck_cam_agent_output_receipt_status",
        ),
    )
    op.create_index(
        "ix_cam_agent_output_receipt_state",
        "cam_agent_image_output_receipts",
        ["tenant_id", "status", "prepared_at"],
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text(
        "SELECT COUNT(*) FROM cam_agent_image_output_receipts"
    )):
        raise RuntimeError(
            "Fenced output receipt history must survive code-only rollback; "
            "populated 0210 downgrade refused"
        )
    op.drop_index(
        "ix_cam_agent_output_receipt_state",
        table_name="cam_agent_image_output_receipts",
    )
    op.drop_table("cam_agent_image_output_receipts")
