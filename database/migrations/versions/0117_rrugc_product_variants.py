"""Add RRUGC product variants and candidate variant matching.

Revision ID: 0117_rrugc_product_variants
Revises: 0116_rrugc_product_page_import
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0117_rrugc_product_variants"
down_revision = "0116_rrugc_product_page_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rrugc_campaigns", sa.Column("product_variant_ids_json", sa.JSON(), nullable=True))

    op.create_table(
        "rrugc_product_variants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("source_variant_id", sa.String(length=120), nullable=False),
        sa.Column("sku", sa.String(length=120), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("color", sa.String(length=120), nullable=True),
        sa.Column("size", sa.String(length=120), nullable=True),
        sa.Column("price_text", sa.String(length=120), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("image_urls_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["rrugc_products.tenant_id", "rrugc_products.id"],
            name="fk_rrugc_product_variant_product",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "product_id",
            "source_variant_id",
            name="uq_rrugc_product_variant_source",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "product_id",
            "id",
            name="uq_rrugc_product_variant_tenant_product_id",
        ),
    )
    op.create_index(
        "ix_rrugc_product_variant_product",
        "rrugc_product_variants",
        ["tenant_id", "product_id", "status", "position"],
        unique=False,
    )

    op.add_column(
        "rrugc_product_references",
        sa.Column("variant_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_rrugc_product_reference_variant",
        "rrugc_product_references",
        "rrugc_product_variants",
        ["tenant_id", "product_id", "variant_id"],
        ["tenant_id", "product_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_rrugc_product_reference_variant",
        "rrugc_product_references",
        ["tenant_id", "product_id", "variant_id", "view_type"],
        unique=False,
    )

    op.add_column("rrugc_candidates", sa.Column("matched_variant_id", sa.String(length=36), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("matched_variant_name", sa.String(length=300), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("matched_color", sa.String(length=120), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("color_match_score", sa.Float(), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("product_shape_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("rrugc_candidates", "product_shape_score")
    op.drop_column("rrugc_candidates", "color_match_score")
    op.drop_column("rrugc_candidates", "matched_color")
    op.drop_column("rrugc_candidates", "matched_variant_name")
    op.drop_column("rrugc_candidates", "matched_variant_id")

    op.drop_index("ix_rrugc_product_reference_variant", table_name="rrugc_product_references")
    op.drop_constraint(
        "fk_rrugc_product_reference_variant",
        "rrugc_product_references",
        type_="foreignkey",
    )
    op.drop_column("rrugc_product_references", "variant_id")

    op.drop_index("ix_rrugc_product_variant_product", table_name="rrugc_product_variants")
    op.drop_table("rrugc_product_variants")

    op.drop_column("rrugc_campaigns", "product_variant_ids_json")
