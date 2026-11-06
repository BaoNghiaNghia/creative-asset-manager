"""Reconcile CAM Etsy Spy projection with the active Windows SQLite listing inventory.

Revision ID: 0196_etsy_spy_inventory
Revises: 0195_etsy_spy_projection

Historical listing observations are never deleted; source snapshots mark the
current representative for each Etsy Listing ID. Backward-compatible defaults
keep existing rows visible until the first successful agent reconciliation.
"""
from alembic import op
import sqlalchemy as sa

revision = "0196_etsy_spy_inventory"
down_revision = "0195_etsy_spy_projection"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("etsy_spy_agents", sa.Column(
        "last_inventory_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("etsy_spy_agents", sa.Column(
        "last_inventory_count", sa.Integer(), nullable=False,
        server_default=sa.text("0")))
    op.add_column("etsy_spy_listings", sa.Column(
        "is_current", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("etsy_spy_listings", sa.Column(
        "has_search", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("etsy_spy_listings", sa.Column(
        "has_shop", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(sa.text(
        "UPDATE etsy_spy_listings SET "
        "has_search = CASE WHEN source_kind = 'search' THEN true ELSE false END, "
        "has_shop = CASE WHEN source_kind = 'shop' THEN true ELSE false END"))
    op.create_index("ix_etsy_spy_listings_active", "etsy_spy_listings",
                    ["tenant_id", "agent_id", "is_current"])


def downgrade():
    op.drop_index("ix_etsy_spy_listings_active", table_name="etsy_spy_listings")
    op.drop_column("etsy_spy_listings", "has_shop")
    op.drop_column("etsy_spy_listings", "has_search")
    op.drop_column("etsy_spy_listings", "is_current")
    op.drop_column("etsy_spy_agents", "last_inventory_count")
    op.drop_column("etsy_spy_agents", "last_inventory_at")
