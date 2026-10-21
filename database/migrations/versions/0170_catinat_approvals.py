"""Catinat manager approval queue with separate audit events.

Revision ID: 0170_catinat_approvals
Revises: 0169_catinat_signed
"""
from alembic import op
import sqlalchemy as sa

revision = "0170_catinat_approvals"
down_revision = "0169_catinat_signed"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_catinat_approval_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("decided_by", sa.String(255)),
        sa.Column("decision_reason", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_catinat_approval_request_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_catinat_approval_tenant_key"),
        sa.CheckConstraint(
            "kind IN ('discrepancy','catalog_mapping','package_conversion','post_close_adjustment','transfer_exception')",
            name="ck_catinat_approval_kind",
        ),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_catinat_approval_status"),
    )
    op.create_index(
        "ix_catinat_approval_tenant_day_status",
        "inventory_catinat_approval_requests", ["tenant_id", "business_date", "status"],
    )
    op.create_table(
        "inventory_catinat_approval_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["inventory_catinat_approval_requests.tenant_id", "inventory_catinat_approval_requests.id"],
            ondelete="RESTRICT", name="fk_catinat_approval_event_request",
        ),
        sa.CheckConstraint("action IN ('requested','approved','rejected')", name="ck_catinat_approval_event_action"),
    )
    op.create_index(
        "ix_catinat_approval_event_request",
        "inventory_catinat_approval_events", ["tenant_id", "request_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_catinat_approval_event_request", table_name="inventory_catinat_approval_events")
    op.drop_table("inventory_catinat_approval_events")
    op.drop_index("ix_catinat_approval_tenant_day_status", table_name="inventory_catinat_approval_requests")
    op.drop_table("inventory_catinat_approval_requests")
