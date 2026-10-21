"""Catinat approved catalog mapping evidence, append-only and NOT activated.

Revision ID: 0174_catinat_catalog_cert
Revises: 0173_catinat_branding_merge
"""
from alembic import op
import sqlalchemy as sa

revision = "0174_catinat_catalog_cert"
down_revision = "0173_catinat_branding_merge"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_catinat_catalog_certifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("approval_request_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("spreadsheet_file_id", sa.String(2048), nullable=False),
        sa.Column("sheet_name", sa.String(128), nullable=False),
        sa.Column("cell_ref", sa.String(16), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_proposal_hash", sa.String(64), nullable=False),
        sa.Column("material_display", sa.String(255), nullable=False),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("location_id", sa.String(36), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=False),
        sa.Column("certified_by", sa.String(255), nullable=False),
        sa.Column("certification_note", sa.String(1000), nullable=False),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["inventory_catinat_approval_requests.tenant_id", "inventory_catinat_approval_requests.id"],
            ondelete="RESTRICT", name="fk_catinat_cert_approval",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "item_id"], ["inventory_items.tenant_id", "inventory_items.id"],
            ondelete="RESTRICT", name="fk_catinat_cert_item",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "location_id"], ["inventory_locations.tenant_id", "inventory_locations.id"],
            ondelete="RESTRICT", name="fk_catinat_cert_location",
        ),
        sa.UniqueConstraint("tenant_id", "approval_request_id", name="uq_catinat_cert_approval"),
        sa.UniqueConstraint(
            "tenant_id", "spreadsheet_file_id", "sheet_name", "cell_ref", "version",
            name="uq_catinat_cert_cell_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_catinat_cert_version"),
        sa.CheckConstraint("length(source_hash) = 64", name="ck_catinat_cert_hash"),
        sa.CheckConstraint("status = 'evidence_only'", name="ck_catinat_cert_not_activated"),
    )
    op.create_index("ix_catinat_cert_tenant_day",
                    "inventory_catinat_catalog_certifications", ["tenant_id", "business_date"])


def downgrade():
    op.drop_index("ix_catinat_cert_tenant_day",
                  table_name="inventory_catinat_catalog_certifications")
    op.drop_table("inventory_catinat_catalog_certifications")
