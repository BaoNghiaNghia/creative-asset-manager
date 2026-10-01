"""Add generic role-based RRUGC reference sets.

Revision ID: 0121_rrugc_reference_sets
Revises: 0120_rrugc_reference_seeds
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0121_rrugc_reference_sets"
down_revision = "0120_rrugc_reference_seeds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_reference_sets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("campaign_id", sa.String(length=36)),
        sa.Column("profile_key", sa.String(length=100)),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_reference_set_campaign",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_rrugc_reference_set_tenant_id",
        ),
    )
    op.create_index(
        "ix_rrugc_reference_set_library",
        "rrugc_reference_sets",
        ["tenant_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_rrugc_reference_set_campaign",
        "rrugc_reference_sets",
        ["tenant_id", "campaign_id", "updated_at"],
    )

    op.create_table(
        "rrugc_reference_set_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("reference_set_id", sa.String(length=36), nullable=False),
        sa.Column("reference_asset_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reference_set_id"],
            ["rrugc_reference_sets.tenant_id", "rrugc_reference_sets.id"],
            name="fk_rrugc_reference_set_item_set",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reference_asset_id"],
            ["rrugc_reference_assets.tenant_id", "rrugc_reference_assets.id"],
            name="fk_rrugc_reference_set_item_asset",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "reference_set_id",
            "role",
            "reference_asset_id",
            name="uq_rrugc_reference_set_item_binding",
        ),
    )
    op.create_index(
        "ix_rrugc_reference_set_item_order",
        "rrugc_reference_set_items",
        ["tenant_id", "reference_set_id", "position", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_reference_set_item_order",
        table_name="rrugc_reference_set_items",
    )
    op.drop_table("rrugc_reference_set_items")
    op.drop_index(
        "ix_rrugc_reference_set_campaign",
        table_name="rrugc_reference_sets",
    )
    op.drop_index(
        "ix_rrugc_reference_set_library",
        table_name="rrugc_reference_sets",
    )
    op.drop_table("rrugc_reference_sets")
