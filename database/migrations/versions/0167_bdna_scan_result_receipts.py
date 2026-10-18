"""Add quarantined metadata-only scan result receipts, NO scan dispatch.

Revision ID: 0167_bdna_scan_result_receipts
Revises: 0166_bdna_scan_authorization_reviews
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0167_bdna_scan_result_receipts"
down_revision = "0166_bdna_scan_authorization_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bc_scan_result_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("lease_id", sa.String(36), nullable=False),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("client_receipt_id", sa.String(36), nullable=False),
        sa.Column("client_payload_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("gallery_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["bc_scan_tasks.tenant_id", "bc_scan_tasks.id"],
            name="fk_bc_scan_result_task_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id", "snapshot_id"],
            ["bc_listing_snapshots.tenant_id", "bc_listing_snapshots.shop_id",
             "bc_listing_snapshots.id"],
            name="fk_bc_scan_result_snapshot_scope", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "task_id", "lease_generation",
            name="uq_bc_scan_result_task_generation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "client_receipt_id",
            name="uq_bc_scan_result_client_receipt",
        ),
        sa.CheckConstraint(
            "lease_generation BETWEEN 1 AND 1000000",
            name="ck_bc_scan_result_generation",
        ),
        sa.CheckConstraint(
            "status = 'quarantined_unverified'",
            name="ck_bc_scan_result_quarantined",
        ),
        sa.CheckConstraint(
            "gallery_count BETWEEN 1 AND 20 AND page_count BETWEEN 1 AND 5",
            name="ck_bc_scan_result_budget",
        ),
    )
    op.create_index(
        "ix_bc_scan_result_shop_received", "bc_scan_result_receipts",
        ["tenant_id", "shop_id", "received_at"],
    )


def downgrade() -> None:
    # A worker's future lost ACK may depend on this immutable row, therefore
    # production rollbacks MUST leave the result ledger intact.
    bind = op.get_bind()
    url = bind.engine.url
    if not (
        os.environ.get("CAM_ALLOW_SCAN_RESULT_DATA_DROP_FOR_TEST") == "1"
        and (
            url.database == "cam_integration"
            or (
                url.get_backend_name() == "sqlite"
                and (url.database or "").rsplit("/", 1)[-1] == "scan-result-test.db"
            )
        )
    ):
        raise RuntimeError("Scan result receipts protected; downgrade denied")
    op.drop_index("ix_bc_scan_result_shop_received", table_name="bc_scan_result_receipts")
    op.drop_table("bc_scan_result_receipts")
