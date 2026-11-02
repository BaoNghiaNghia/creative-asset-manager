"""Immutable Products Slot 7 visor accent selections (additive, no backfill).

Revision ID: 0192_products_visor_accent_v1
Revises: 0191_tiktokshop_scout_agent_workspace
"""
from alembic import op
import sqlalchemy as sa

revision = "0192_products_visor_accent_v1"
down_revision = "0191_tiktokshop_scout_agent_workspace"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrugc_visor_accent_selections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("source_plan_id", sa.String(36), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("material_remote_file_id", sa.String(255), nullable=False),
        sa.Column("reference_sha256", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(48), nullable=False),
        sa.Column("mask_png", sa.LargeBinary(), nullable=False),
        sa.Column("mask_sha256", sa.String(64), nullable=False),
        sa.Column("layer_sha256", sa.String(64), nullable=False),
        sa.Column("selection_method", sa.String(40), nullable=False),
        sa.Column("selected_pixels", sa.Integer(), nullable=False),
        sa.Column("thread_pixels", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_plan_id"], ["rrugc_source_plans.id"],
            name="fk_rrugc_visor_accent_source", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "source_plan_id", "source_revision",
            "material_remote_file_id", "algorithm_version",
            name="uq_rrugc_visor_accent_material",
        ),
        sa.CheckConstraint("selected_pixels >= 0 AND thread_pixels > 0",
                           name="ck_rrugc_visor_accent_pixel_count"),
        sa.CheckConstraint("selected_pixels * 10 <= thread_pixels * 3",
                           name="ck_rrugc_visor_accent_max_30_percent"),
    )
    op.create_index(
        "ix_rrugc_visor_accent_source", "rrugc_visor_accent_selections",
        ["tenant_id", "source_plan_id", "source_revision"],
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM rrugc_visor_accent_selections")):
        raise RuntimeError("visor_accent_masks_must_be_empty_before_downgrade")
    op.drop_index("ix_rrugc_visor_accent_source",
                  table_name="rrugc_visor_accent_selections")
    op.drop_table("rrugc_visor_accent_selections")
