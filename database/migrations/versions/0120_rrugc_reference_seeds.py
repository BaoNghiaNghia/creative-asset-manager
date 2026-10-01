"""Add campaign/profile-scoped Reference Library seeds.

Revision ID: 0120_rrugc_reference_seeds
Revises: 0119_rrugc_reference_assets
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0120_rrugc_reference_seeds"
down_revision = "0119_rrugc_reference_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_reference_seeds",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("reference_asset_id", sa.String(length=36), nullable=False),
        sa.Column("profile_key", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_reference_seed_campaign",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reference_asset_id"],
            ["rrugc_reference_assets.tenant_id", "rrugc_reference_assets.id"],
            name="fk_rrugc_reference_seed_asset",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "profile_key",
            "reference_asset_id",
            name="uq_rrugc_reference_seed_scope",
        ),
    )
    op.create_index(
        "ix_rrugc_reference_seed_scope",
        "rrugc_reference_seeds",
        ["tenant_id", "campaign_id", "profile_key", "label", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_reference_seed_scope",
        table_name="rrugc_reference_seeds",
    )
    op.drop_table("rrugc_reference_seeds")
