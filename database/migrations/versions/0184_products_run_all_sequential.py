"""Durable and idempotent Stage 2 Products Run all (slots 1 through 7).

Revision ID: 0184_products_run_all_sequential
Revises: 0183_allow_gatorhats_scene_stages
"""
from alembic import op
import sqlalchemy as sa

revision = "0184_products_run_all_sequential"
down_revision = "0183_allow_gatorhats_scene_stages"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrugc_products_run_all",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("source_plan_id", sa.String(36), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("color_key", sa.String(80), nullable=False),
        sa.Column("started_by_user_id", sa.String(255), nullable=False),
        sa.Column("colorway_skill_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_slot", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["source_plan_id"], ["rrugc_source_plans.id"],
                                name="fk_rrugc_products_run_all_source", ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "source_plan_id", "source_revision", "color_key",
                            name="uq_rrugc_products_run_all_source_color"),
        sa.CheckConstraint("status IN ('running','blocked','completed')",
                           name="ck_rrugc_products_run_all_status"),
        sa.CheckConstraint("current_slot BETWEEN 1 AND 7",
                           name="ck_rrugc_products_run_all_slot"),
    )
    op.create_index("ix_rrugc_products_run_all_active", "rrugc_products_run_all",
                    ["status", "updated_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM rrugc_products_run_all")):
        raise RuntimeError("cannot_drop_durable_products_run_all_history")
    op.drop_index("ix_rrugc_products_run_all_active", table_name="rrugc_products_run_all")
    op.drop_table("rrugc_products_run_all")
