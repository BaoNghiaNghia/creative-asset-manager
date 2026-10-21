"""Add unique signed AM/PM Catinat stock snapshots without touching live Sheets.

Revision ID: 0169_catinat_signed
Revises: 0168_branding_dna_outline_versions
"""
from alembic import op
import sqlalchemy as sa

revision = "0169_catinat_signed"
down_revision = "0168_branding_dna_outline_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_catinat_signed_shifts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("shift", sa.String(16), nullable=False),
        sa.Column("source_spreadsheet_file_id", sa.String(2048), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("counts_json", sa.JSON(), nullable=False),
        sa.Column("confirmed_by", sa.String(255), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "business_date", "shift",
                            name="uq_catinat_signed_shift_tenant_day"),
        sa.CheckConstraint("shift IN ('morning','evening')", name="ck_catinat_signed_shift_name"),
        sa.CheckConstraint("length(source_hash) = 64", name="ck_catinat_signed_shift_source_hash"),
        sa.CheckConstraint("length(snapshot_hash) = 64", name="ck_catinat_signed_shift_hash"),
    )
    op.create_index("ix_catinat_signed_shifts_date",
                    "inventory_catinat_signed_shifts",
                    ["tenant_id", "business_date", "shift"])


def downgrade():
    op.drop_index("ix_catinat_signed_shifts_date", table_name="inventory_catinat_signed_shifts")
    op.drop_table("inventory_catinat_signed_shifts")
