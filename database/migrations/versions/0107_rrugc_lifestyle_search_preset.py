"""Use a real-person lifestyle search preset and allow existing headwear.

Revision ID: 0107_rrugc_lifestyle_search_preset
Revises: 0106_rrugc_single_scout_agent
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0107_rrugc_lifestyle_search_preset"
down_revision = "0106_rrugc_single_scout_agent"
branch_labels = None
depends_on = None

LEGACY_QUERY = "happy woman casual outdoor candid"
SEARCH_QUERIES = [
    "authentic candid lifestyle portrait",
    "casual family candid lifestyle photo",
    "parent child outdoor candid photo",
    "dad child candid lifestyle photo",
    "casual man selfie natural light",
    "casual woman selfie natural light",
    "everyday casual portrait at home",
    "outdoor candid portrait natural light",
    "embroidered baseball cap casual selfie",
    "corduroy cap casual lifestyle portrait",
]


def _campaigns() -> sa.Table:
    return sa.table(
        "rrugc_campaigns",
        sa.column("query", sa.String(length=500)),
        sa.column("search_queries_json", sa.JSON()),
        sa.column("reject_headwear", sa.Boolean()),
    )


def upgrade() -> None:
    campaigns = _campaigns()
    bind = op.get_bind()

    # The current reference style accepts both bare heads and people already
    # wearing hats. A campaign can still turn this rejection back on manually.
    bind.execute(campaigns.update().values(reject_headwear=False))

    # Only replace the old one-keyword starter campaign. Custom campaign
    # keyword sets are preserved.
    bind.execute(
        campaigns.update()
        .where(campaigns.c.query == LEGACY_QUERY)
        .values(
            query=SEARCH_QUERIES[0],
            search_queries_json=SEARCH_QUERIES,
        )
    )


def downgrade() -> None:
    campaigns = _campaigns()
    bind = op.get_bind()
    bind.execute(campaigns.update().values(reject_headwear=True))
    bind.execute(
        campaigns.update()
        .where(campaigns.c.query == SEARCH_QUERIES[0])
        .values(
            query=LEGACY_QUERY,
            search_queries_json=[LEGACY_QUERY],
        )
    )
