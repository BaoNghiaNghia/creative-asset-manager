"""Add RRUGC product page import metadata.

Revision ID: 0116_rrugc_product_page_import
Revises: 0115_rrugc_ref_good_authoritative
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0116_rrugc_product_page_import"
down_revision = "0115_rrugc_ref_good_authoritative"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rrugc_products", sa.Column("source_url", sa.String(length=2048), nullable=True))
    op.add_column("rrugc_products", sa.Column("source_host", sa.String(length=255), nullable=True))
    op.add_column("rrugc_products", sa.Column("brand", sa.String(length=200), nullable=True))
    op.add_column("rrugc_products", sa.Column("source_description", sa.Text(), nullable=True))
    op.add_column("rrugc_products", sa.Column("source_category", sa.String(length=200), nullable=True))
    op.add_column("rrugc_products", sa.Column("source_price_text", sa.String(length=120), nullable=True))
    op.add_column("rrugc_products", sa.Column("source_currency", sa.String(length=16), nullable=True))
    op.add_column("rrugc_products", sa.Column("source_images_json", sa.JSON(), nullable=True))
    op.add_column("rrugc_products", sa.Column("source_variants_json", sa.JSON(), nullable=True))
    op.add_column("rrugc_products", sa.Column("source_metadata_json", sa.JSON(), nullable=True))
    op.add_column(
        "rrugc_products",
        sa.Column("source_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_rrugc_product_tenant_source_url",
        "rrugc_products",
        ["tenant_id", "source_url"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_rrugc_product_tenant_source_url", table_name="rrugc_products")
    op.drop_column("rrugc_products", "source_fetched_at")
    op.drop_column("rrugc_products", "source_metadata_json")
    op.drop_column("rrugc_products", "source_variants_json")
    op.drop_column("rrugc_products", "source_images_json")
    op.drop_column("rrugc_products", "source_currency")
    op.drop_column("rrugc_products", "source_price_text")
    op.drop_column("rrugc_products", "source_category")
    op.drop_column("rrugc_products", "source_description")
    op.drop_column("rrugc_products", "brand")
    op.drop_column("rrugc_products", "source_host")
    op.drop_column("rrugc_products", "source_url")
