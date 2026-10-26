"""Persist per-design Stage 1 embroidery size for future Products jobs.

Revision ID: 0186_rrugc_stage1_front_design_size
Revises: 0185_rrugc_front_placement_settings
"""
from alembic import op
import sqlalchemy as sa

revision = "0186_rrugc_stage1_front_design_size"
down_revision = "0185_rrugc_front_placement_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "rrugc_image_output_versions",
        sa.Column("front_design_size", sa.Integer(), nullable=False, server_default="100"),
    )
    op.create_check_constraint(
        "ck_rrugc_stage1_front_design_size",
        "rrugc_image_output_versions",
        "front_design_size >= 40 AND front_design_size <= 200",
    )


def downgrade():
    op.drop_constraint(
        "ck_rrugc_stage1_front_design_size",
        "rrugc_image_output_versions",
        type_="check",
    )
    op.drop_column("rrugc_image_output_versions", "front_design_size")
