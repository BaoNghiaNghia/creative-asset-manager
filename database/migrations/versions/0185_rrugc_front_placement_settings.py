"""Durable tenant-scoped GatorHats front embroidery placement calibration.

Revision ID: 0185_rrugc_front_placement_settings
Revises: 0184_products_run_all_sequential
"""
from alembic import op
import sqlalchemy as sa

revision = "0185_rrugc_front_placement_settings"
down_revision = "0184_products_run_all_sequential"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrugc_front_placement_settings",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("guide_left", sa.Float(), nullable=False),
        sa.Column("guide_top", sa.Float(), nullable=False),
        sa.Column("guide_width", sa.Float(), nullable=False),
        sa.Column("guide_height", sa.Float(), nullable=False),
        sa.Column("upper_line", sa.Float(), nullable=False),
        sa.Column("lower_line", sa.Float(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("guide_left >= 0 AND guide_left <= 1", name="ck_rrugc_front_placement_left"),
        sa.CheckConstraint("guide_top >= 0 AND guide_top <= 1", name="ck_rrugc_front_placement_top"),
        sa.CheckConstraint("guide_width >= 0.10 AND guide_width <= 0.95 AND guide_left + guide_width <= 1",
                           name="ck_rrugc_front_placement_width"),
        sa.CheckConstraint("guide_height >= 0.06 AND guide_height <= 0.65 AND guide_top + guide_height <= 1",
                           name="ck_rrugc_front_placement_height"),
        sa.CheckConstraint("upper_line >= 0 AND upper_line < lower_line AND lower_line <= 1",
                           name="ck_rrugc_front_placement_lines"),
        sa.CheckConstraint("revision >= 1", name="ck_rrugc_front_placement_revision"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM rrugc_front_placement_settings")):
        raise RuntimeError("cannot_drop_persisted_front_placement_settings")
    op.drop_table("rrugc_front_placement_settings")
