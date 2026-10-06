"""Persist Stage 0 keyword pick/used state.

Revision ID: 0136_rrugc_keyword_pick_state
Revises: 0135_rrugc_multi_scout_agents
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0136_rrugc_keyword_pick_state"
down_revision = "0135_rrugc_multi_scout_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("picked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("picked_by_user_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_rrugc_keyword_volume_picked",
        "rrugc_keyword_volumes",
        ["tenant_id", "picked"],
        unique=False,
    )
    op.alter_column("rrugc_keyword_volumes", "picked", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_keyword_volume_picked",
        table_name="rrugc_keyword_volumes",
    )
    op.drop_column("rrugc_keyword_volumes", "picked_by_user_id")
    op.drop_column("rrugc_keyword_volumes", "picked_at")
    op.drop_column("rrugc_keyword_volumes", "picked")
