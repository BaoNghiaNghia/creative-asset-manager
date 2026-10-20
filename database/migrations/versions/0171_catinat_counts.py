"""Append-only Catinat verified physical-count observations.

Revision ID: 0171_catinat_counts
Revises: 0170_catinat_approvals
"""
from alembic import op
import sqlalchemy as sa

revision = "0171_catinat_counts"
down_revision = "0170_catinat_approvals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_catinat_count_confirmations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("shift", sa.String(16), nullable=False),
        sa.Column("spreadsheet_file_id", sa.String(2048), nullable=False),
        sa.Column("sheet_name", sa.String(128), nullable=False),
        sa.Column("cell_ref", sa.String(16), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("material_display", sa.String(255), nullable=False),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("physical_quantity", sa.String(64), nullable=False),
        sa.Column("physical_raw", sa.String(255), nullable=False),
        sa.Column("confirmed_by", sa.String(255), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "tenant_id", "business_date", "shift", "sheet_name",
            "cell_ref", "source_hash", name="uq_catinat_count_confirmation_source_cell",
        ),
        sa.CheckConstraint("shift IN ('morning','evening')", name="ck_catinat_count_confirmation_shift"),
        sa.CheckConstraint("length(source_hash) = 64", name="ck_catinat_count_confirmation_hash"),
    )
    op.create_index("ix_catinat_confirmations_day", "inventory_catinat_count_confirmations",
                    ["tenant_id", "business_date", "shift"])


def downgrade():
    op.drop_index("ix_catinat_confirmations_day", table_name="inventory_catinat_count_confirmations")
    op.drop_table("inventory_catinat_count_confirmations")
