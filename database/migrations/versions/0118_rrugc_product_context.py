"""Add RRUGC Pinterest discovery mode and product context profile.

Revision ID: 0118_rrugc_product_context
Revises: 0117_rrugc_product_variants
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0118_rrugc_product_context"
down_revision = "0117_rrugc_product_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_campaigns",
        sa.Column(
            "discovery_mode",
            sa.String(length=32),
            nullable=False,
            server_default="keyword",
        ),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("product_context_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rrugc_campaigns", "product_context_json")
    op.drop_column("rrugc_campaigns", "discovery_mode")
