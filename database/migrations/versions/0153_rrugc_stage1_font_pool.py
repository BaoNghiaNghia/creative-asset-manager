"""Persist Stage 1 DaFont selection per job for retry-stable random font plans.

Revision ID: 0153_rrugc_stage1_font_pool
Revises: 0152_rrugc_stage1_output_font_metadata
"""
from alembic import op
import sqlalchemy as sa

revision = "0153_rrugc_stage1_font_pool"
down_revision = "0152_rrugc_stage1_output_font_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rrugc_keyword_image_jobs",
                  sa.Column("font_plan_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("rrugc_keyword_image_jobs", "font_plan_json")
