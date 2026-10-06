"""Add Stage 3 synthetic review card fields.

Revision ID: 0133_rrugc_stage3_review_copy
Revises: 0132_rrugc_stage3_ugc_analysis
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0133_rrugc_stage3_review_copy"
down_revision = "0132_rrugc_stage3_ugc_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_stage3_analyses",
        sa.Column("reviewer_name", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "rrugc_stage3_analyses",
        sa.Column("star_rating", sa.Integer(), nullable=True),
    )
    op.add_column(
        "rrugc_stage3_analyses",
        sa.Column("review_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "rrugc_stage3_analyses",
        sa.Column("review_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rrugc_stage3_analyses", "review_generated_at")
    op.drop_column("rrugc_stage3_analyses", "review_text")
    op.drop_column("rrugc_stage3_analyses", "star_rating")
    op.drop_column("rrugc_stage3_analyses", "reviewer_name")
