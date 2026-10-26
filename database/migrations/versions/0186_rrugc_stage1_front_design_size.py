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
    # Batch produces native ALTER on PostgreSQL and a safe table copy on
    # SQLite, where ALTER TABLE ADD CONSTRAINT is unsupported.
    with op.batch_alter_table("rrugc_image_output_versions") as batch:
        batch.add_column(
            sa.Column("front_design_size", sa.Integer(), nullable=False, server_default="100"),
        )
        batch.create_check_constraint(
            "ck_rrugc_stage1_front_design_size",
            "front_design_size >= 40 AND front_design_size <= 200",
        )


def downgrade():
    with op.batch_alter_table("rrugc_image_output_versions") as batch:
        batch.drop_constraint("ck_rrugc_stage1_front_design_size", type_="check")
        batch.drop_column("front_design_size")
