"""Persist independent Stage 0 keyword favorites.

Revision ID: 0137_rrugc_keyword_favorites
Revises: 0136_rrugc_keyword_pick_state
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0137_rrugc_keyword_favorites"
down_revision = "0136_rrugc_keyword_pick_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("favorite_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("favorite_by_user_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_rrugc_keyword_volume_favorite",
        "rrugc_keyword_volumes",
        ["tenant_id", "favorite"],
        unique=False,
    )
    op.alter_column("rrugc_keyword_volumes", "favorite", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_rrugc_keyword_volume_favorite", table_name="rrugc_keyword_volumes")
    op.drop_column("rrugc_keyword_volumes", "favorite_by_user_id")
    op.drop_column("rrugc_keyword_volumes", "favorite_at")
    op.drop_column("rrugc_keyword_volumes", "favorite")
