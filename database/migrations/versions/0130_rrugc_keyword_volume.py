"""Add independent RRUGC Stage 0 keyword volume cache.

Revision ID: 0130_rrugc_keyword_volume
Revises: 0129_rrugc_stage2_output_history_guard
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0130_rrugc_keyword_volume"
down_revision = "0129_rrugc_stage2_output_history_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_keyword_volumes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("keyword", sa.String(length=500), nullable=False),
        sa.Column("keyword_normalized", sa.String(length=500), nullable=False),
        sa.Column("search_volume", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("competition", sa.String(length=32), nullable=True),
        sa.Column("cpc_low", sa.Float(), nullable=True),
        sa.Column("cpc_high", sa.Float(), nullable=True),
        sa.Column(
            "provider",
            sa.String(length=64),
            nullable=False,
            server_default="aebrowse_google_ads",
        ),
        sa.Column("provider_account", sa.String(length=255), nullable=True),
        sa.Column("provider_customer_id", sa.String(length=64), nullable=True),
        sa.Column("provider_raw_json", sa.JSON(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "keyword_normalized",
            name="uq_rrugc_keyword_volume_keyword",
        ),
    )
    op.create_index(
        "ix_rrugc_keyword_volume_search",
        "rrugc_keyword_volumes",
        ["tenant_id", "search_volume"],
        unique=False,
    )
    op.create_index(
        "ix_rrugc_keyword_volume_fetched",
        "rrugc_keyword_volumes",
        ["tenant_id", "fetched_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_keyword_volume_fetched",
        table_name="rrugc_keyword_volumes",
    )
    op.drop_index(
        "ix_rrugc_keyword_volume_search",
        table_name="rrugc_keyword_volumes",
    )
    op.drop_table("rrugc_keyword_volumes")
