"""Persist each Stage 1 Skill output's original relative filename.

Revision ID: 0145_rrugc_stage1_output_names
Revises: 0144_rrugc_skill_stage_defaults
"""
from alembic import op
import sqlalchemy as sa

revision = "0145_rrugc_stage1_output_names"
down_revision = "0144_rrugc_skill_stage_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_image_output_versions",
        sa.Column("output_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rrugc_image_output_versions", "output_name")
