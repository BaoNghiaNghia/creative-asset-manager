"""Add durable terminal failure receipts; no scan activation.

Revision ID: 0165_bdna_scan_failure_receipts
Revises: 0164_branding_dna_shop_rag
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0165_bdna_scan_failure_receipts"
down_revision = "0164_branding_dna_shop_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ux_bc_scan_task_tenant_identity", "bc_scan_tasks",
                    ["tenant_id", "id"], unique=True)
    op.create_table(
        "bc_scan_failure_receipts",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("lease_generation", sa.Integer(), primary_key=True),
        sa.Column("lease_id", sa.String(36), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("retry_after", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "task_id"],
                                ["bc_scan_tasks.tenant_id", "bc_scan_tasks.id"],
                                name="fk_bc_scan_receipt_task_scope", ondelete="RESTRICT"),
        sa.CheckConstraint("lease_generation BETWEEN 1 AND 1000000",
                           name="ck_bc_scan_receipt_generation"),
        sa.CheckConstraint("status IN ('retry','blocked')",
                           name="ck_bc_scan_receipt_status"),
    )
    op.create_index("ix_bc_scan_receipt_tenant_task", "bc_scan_failure_receipts",
                    ["tenant_id", "task_id"])


def downgrade() -> None:
    # A deployed worker may be waiting for ACK of an immutable receipt.
    # Do not drop evidence of its committed state in automatic rollback.
    bind = op.get_bind()
    url = bind.engine.url
    if not (os.environ.get("CAM_ALLOW_SCAN_RECEIPT_DATA_DROP_FOR_TEST") == "1"
            and (url.database == "cam_integration" or
                 url.get_backend_name() == "sqlite" and
                 (url.database or "").rsplit("/", 1)[-1] == "scan-receipt-test.db")):
        raise RuntimeError("Scan failure receipts protected; downgrade denied")
    op.drop_index("ix_bc_scan_receipt_tenant_task", table_name="bc_scan_failure_receipts")
    op.drop_table("bc_scan_failure_receipts")
    op.drop_index("ux_bc_scan_task_tenant_identity", table_name="bc_scan_tasks")
