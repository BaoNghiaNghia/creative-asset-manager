"""Add RRUGC campaign search query sets.

Revision ID: 0104_rrugc_campaign_search_queries
Revises: 0103_rrugc_pinterest_autoscout
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0104_rrugc_campaign_search_queries"
down_revision = "0103_rrugc_pinterest_autoscout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_campaigns",
        sa.Column("search_queries_json", sa.JSON(), nullable=True),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, query FROM rrugc_campaigns")).all()
    campaign = sa.table(
        "rrugc_campaigns",
        sa.column("id", sa.String()),
        sa.column("search_queries_json", sa.JSON()),
    )
    for row in rows:
        query = str(row.query or "").strip()
        bind.execute(
            campaign.update()
            .where(campaign.c.id == row.id)
            .values(search_queries_json=[query] if query else [])
        )


def downgrade() -> None:
    op.drop_column("rrugc_campaigns", "search_queries_json")
