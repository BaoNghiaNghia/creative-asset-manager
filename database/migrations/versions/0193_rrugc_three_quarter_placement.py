"""Tenant-scoped, per-color three-quarter calibration for Blueprint previews.

Revision ID: 0193_rrugc_three_quarter_placement
Revises: 0192_products_visor_accent_v1
"""
from alembic import op
import sqlalchemy as sa

revision = "0193_rrugc_three_quarter_placement"
down_revision = "0192_products_visor_accent_v1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrugc_three_quarter_placement_settings",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("color_key", sa.String(64), primary_key=True),
        sa.Column("corners_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_rrugc_3q_revision"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM rrugc_three_quarter_placement_settings")):
        raise RuntimeError("cannot_drop_persisted_three_quarter_placements")
    op.drop_table("rrugc_three_quarter_placement_settings")
