"""Catinat Q32 transfer handoff/receipt tracker (no automatic stock posting).

Revision ID: 0172_catinat_transfers
Revises: 0171_catinat_counts
"""
from alembic import op
import sqlalchemy as sa

revision = "0172_catinat_transfers"
down_revision = "0171_catinat_counts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_catinat_transfers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("reference_key", sa.String(128), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("source_location_id", sa.String(36), nullable=False),
        sa.Column("destination_location_id", sa.String(36), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("dispatched_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("received_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dispatched_by", sa.String(255), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_last_at", sa.DateTime(timezone=True)),
        sa.Column("ledger_posted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "item_id"], ["inventory_items.tenant_id", "inventory_items.id"], ondelete="RESTRICT", name="fk_catinat_transfer_item"),
        sa.ForeignKeyConstraint(["tenant_id", "source_location_id"], ["inventory_locations.tenant_id", "inventory_locations.id"], ondelete="RESTRICT", name="fk_catinat_transfer_source"),
        sa.ForeignKeyConstraint(["tenant_id", "destination_location_id"], ["inventory_locations.tenant_id", "inventory_locations.id"], ondelete="RESTRICT", name="fk_catinat_transfer_destination"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_catinat_transfer_tenant_id"),
        sa.UniqueConstraint("tenant_id", "reference_key", name="uq_catinat_transfer_tenant_reference"),
        sa.CheckConstraint("dispatched_quantity > 0", name="ck_catinat_transfer_dispatched_positive"),
        sa.CheckConstraint("received_quantity >= 0 AND received_quantity <= dispatched_quantity", name="ck_catinat_transfer_receipt_bounds"),
        sa.CheckConstraint("status IN ('pending_receipt','partial_receipt','received')", name="ck_catinat_transfer_status"),
    )
    op.create_index("ix_catinat_transfer_day_status", "inventory_catinat_transfers", ["tenant_id", "business_date", "status"])
    op.create_table(
        "inventory_catinat_transfer_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("transfer_id", sa.String(36), nullable=False),
        sa.Column("event_key", sa.String(128), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "transfer_id"],
                                ["inventory_catinat_transfers.tenant_id", "inventory_catinat_transfers.id"],
                                ondelete="RESTRICT", name="fk_catinat_transfer_event_transfer"),
        sa.UniqueConstraint("tenant_id", "transfer_id", "event_key", name="uq_catinat_transfer_event_key"),
        sa.CheckConstraint("action IN ('dispatched','received')", name="ck_catinat_transfer_event_action"),
        sa.CheckConstraint("quantity > 0", name="ck_catinat_transfer_event_quantity"),
    )
    op.create_index("ix_catinat_transfer_events_transfer", "inventory_catinat_transfer_events", ["tenant_id", "transfer_id", "created_at"])


def downgrade():
    op.drop_index("ix_catinat_transfer_events_transfer", table_name="inventory_catinat_transfer_events")
    op.drop_table("inventory_catinat_transfer_events")
    op.drop_index("ix_catinat_transfer_day_status", table_name="inventory_catinat_transfers")
    op.drop_table("inventory_catinat_transfers")
