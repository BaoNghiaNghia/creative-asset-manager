"""Tenant-scoped reversible Etsy Spy phrase blacklist.

Revision ID: 0197_etsy_spy_blacklist
Revises: 0196_etsy_spy_inventory
"""
from alembic import op
import sqlalchemy as sa

revision = "0197_etsy_spy_blacklist"
down_revision = "0196_etsy_spy_inventory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_blacklist",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("phrase", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "phrase"),
    )


def downgrade():
    op.drop_table("etsy_spy_blacklist")
