"""Add generic RRUGC Reference Library assets.

Revision ID: 0119_rrugc_reference_assets
Revises: 0118_rrugc_product_context
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0119_rrugc_reference_assets"
down_revision = "0118_rrugc_product_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_reference_assets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("original_filename", sa.String(length=500)),
        sa.Column("source_campaign_id", sa.String(length=36)),
        sa.Column("source_candidate_id", sa.String(length=36)),
        sa.Column("profile_key", sa.String(length=100)),
        sa.Column("reference_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("image_format", sa.String(length=16)),
        sa.Column("tags_json", sa.JSON()),
        sa.Column("themes_json", sa.JSON()),
        sa.Column("quality_score", sa.Float()),
        sa.Column("visual_score", sa.Float()),
        sa.Column("context_score", sa.Float()),
        sa.Column(
            "usage_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("remote_file_id", sa.String(length=255), nullable=False),
        sa.Column("remote_folder_id", sa.String(length=255)),
        sa.Column("web_url", sa.Text()),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_rrugc_reference_asset_tenant_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_key",
            name="uq_rrugc_reference_asset_source",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "content_hash",
            name="uq_rrugc_reference_asset_content_hash",
        ),
    )
    op.create_index(
        "ix_rrugc_reference_asset_library",
        "rrugc_reference_assets",
        ["tenant_id", "status", "source_type", "updated_at"],
    )
    op.create_index(
        "ix_rrugc_reference_asset_campaign",
        "rrugc_reference_assets",
        ["tenant_id", "source_campaign_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_reference_asset_campaign",
        table_name="rrugc_reference_assets",
    )
    op.drop_index(
        "ix_rrugc_reference_asset_library",
        table_name="rrugc_reference_assets",
    )
    op.drop_table("rrugc_reference_assets")
