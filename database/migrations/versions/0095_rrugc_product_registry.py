"""Add Realistic Review UGC product reference registry.

Revision ID: 0095_rrugc_product_registry
Revises: 0094_rrugc_reference_analysis
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0095_rrugc_product_registry"
down_revision = "0094_rrugc_reference_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_products",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("sku", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("product_type", sa.String(length=64), nullable=False, server_default="hat"),
        sa.Column("color", sa.String(length=120)),
        sa.Column("material", sa.String(length=200)),
        sa.Column("crown_profile", sa.String(length=64)),
        sa.Column("crown_height_mm", sa.Float()),
        sa.Column("brim_style", sa.String(length=64)),
        sa.Column("brim_length_mm", sa.Float()),
        sa.Column("circumference_mm", sa.Float()),
        sa.Column("logo_position", sa.String(length=120)),
        sa.Column("fit_notes", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rrugc_product_tenant_id"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_rrugc_product_tenant_sku"),
    )
    op.create_index(
        "ix_rrugc_product_tenant_status",
        "rrugc_products",
        ["tenant_id", "status", "updated_at"],
    )

    op.create_table(
        "rrugc_product_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("view_type", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255)),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("image_format", sa.String(length=16), nullable=False),
        sa.Column("remote_file_id", sa.String(length=255)),
        sa.Column("remote_folder_id", sa.String(length=255)),
        sa.Column("web_url", sa.Text()),
        sa.Column("reused_storage", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["rrugc_products.tenant_id", "rrugc_products.id"],
            name="fk_rrugc_product_reference_product",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "product_id",
            "view_type",
            "version",
            name="uq_rrugc_product_reference_version",
        ),
    )
    op.create_index(
        "ix_rrugc_product_reference_product_view",
        "rrugc_product_references",
        ["tenant_id", "product_id", "view_type", "version"],
    )
    op.create_index(
        "ix_rrugc_product_reference_hash",
        "rrugc_product_references",
        ["tenant_id", "content_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_product_reference_hash",
        table_name="rrugc_product_references",
    )
    op.drop_index(
        "ix_rrugc_product_reference_product_view",
        table_name="rrugc_product_references",
    )
    op.drop_table("rrugc_product_references")
    op.drop_index("ix_rrugc_product_tenant_status", table_name="rrugc_products")
    op.drop_table("rrugc_products")
