"""Persistent Later topic seeds, related listing matches, and dismissals.

Revision ID: 0202_etsy_spy_later
Revises: 0201_etsy_spy_parent_keywords
"""
from alembic import op
import sqlalchemy as sa

revision = "0202_etsy_spy_later"
down_revision = "0201_etsy_spy_parent_keywords"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_later_seeds",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("listing_key", sa.String(150), nullable=False),
        sa.Column("title", sa.String(700), nullable=False),
        sa.Column("analysis_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "listing_key"),
    )
    op.create_table(
        "etsy_spy_later_matches",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("listing_key", sa.String(150), nullable=False),
        sa.Column("seed_listing_key", sa.String(150), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(180), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="matched"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "listing_key"),
    )
    op.create_index(
        "ix_etsy_spy_later_tenant_state", "etsy_spy_later_matches",
        ["tenant_id", "state"],
    )


def downgrade():
    op.drop_index("ix_etsy_spy_later_tenant_state", table_name="etsy_spy_later_matches")
    op.drop_table("etsy_spy_later_matches")
    op.drop_table("etsy_spy_later_seeds")
