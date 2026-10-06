"""Allow multiple active RRUGC Pinterest Scout agents per tenant.

Revision ID: 0135_rrugc_multi_scout_agents
Revises: 0134_rrugc_keyword_min_words
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0135_rrugc_multi_scout_agents"
down_revision = "0134_rrugc_keyword_min_words"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_rrugc_scout_agent_one_active_per_tenant"


def upgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="rrugc_scout_agents")


def downgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "rrugc_scout_agents",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("active = true"),
        sqlite_where=sa.text("active = 1"),
    )
