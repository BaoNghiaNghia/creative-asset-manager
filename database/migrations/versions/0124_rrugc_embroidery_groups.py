"""Share RRUGC source discovery by color-invariant embroidery signature.

Revision ID: 0124_rrugc_embroidery_groups
Revises: 0123_rrugc_drive_source_plans
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0124_rrugc_embroidery_groups"
down_revision = "0123_rrugc_drive_source_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_source_plans",
        sa.Column("embroidery_signature", sa.String(length=64)),
    )
    op.create_index(
        "ix_rrugc_source_plan_embroidery_signature",
        "rrugc_source_plans",
        ["tenant_id", "embroidery_signature"],
    )
    op.alter_column(
        "rrugc_source_plans",
        "target_count",
        server_default=sa.text("50"),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            "UPDATE rrugc_source_plans "
            "SET target_count = 50 "
            "WHERE target_count <> 50"
        )
    )
    op.execute(
        sa.text(
            "UPDATE rrugc_campaigns "
            "SET target_count = 50 "
            "WHERE id IN ("
            "SELECT DISTINCT campaign_id FROM rrugc_source_plans "
            "WHERE campaign_id IS NOT NULL"
            ")"
        )
    )


def downgrade() -> None:
    op.alter_column(
        "rrugc_source_plans",
        "target_count",
        server_default=sa.text("20"),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.drop_index(
        "ix_rrugc_source_plan_embroidery_signature",
        table_name="rrugc_source_plans",
    )
    op.drop_column("rrugc_source_plans", "embroidery_signature")
