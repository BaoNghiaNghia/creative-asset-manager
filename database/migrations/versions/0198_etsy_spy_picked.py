"""Persistent per-tenant Picked listing IDs, independent of Scout inventory.

Revision ID: 0198_etsy_spy_picked
Revises: 0197_etsy_spy_blacklist
"""
from alembic import op
import sqlalchemy as sa

revision = "0198_etsy_spy_picked"
down_revision = "0197_etsy_spy_blacklist"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_picked",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("listing_key", sa.String(150), nullable=False),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "listing_key"),
    )
    op.create_index("ix_etsy_spy_picked_tenant_at", "etsy_spy_picked",
                    ["tenant_id", "picked_at"])


def downgrade():
    op.drop_index("ix_etsy_spy_picked_tenant_at", table_name="etsy_spy_picked")
    op.drop_table("etsy_spy_picked")
