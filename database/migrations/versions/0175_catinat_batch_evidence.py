"""Catinat staff batch evidence + waste manager queue, without stock posting.

Revision ID: 0175_catinat_batch_evidence
Revises: 0174_catinat_catalog_cert
"""
from alembic import op
import sqlalchemy as sa

revision = "0175_catinat_batch_evidence"
down_revision = "0174_catinat_catalog_cert"
branch_labels = None
depends_on = None

OLD_KINDS = "kind IN ('discrepancy','catalog_mapping','package_conversion','post_close_adjustment','transfer_exception')"
NEW_KINDS = "kind IN ('discrepancy','catalog_mapping','package_conversion','post_close_adjustment','transfer_exception','waste_disposal')"


def upgrade():
    op.create_table(
        "inventory_catinat_batch_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("spreadsheet_file_id", sa.String(2048), nullable=False),
        sa.Column("sheet_name", sa.String(128), nullable=False),
        sa.Column("cell_ref", sa.String(16), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("material_display", sa.String(255), nullable=False),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("lot_reference", sa.String(128), nullable=False),
        sa.Column("event_kind", sa.String(16), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("package_expiry_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("attested_by", sa.String(255), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_catinat_batch_tenant_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_catinat_batch_tenant_key"),
        sa.CheckConstraint("event_kind IN ('prepared','opened')", name="ck_catinat_batch_event_kind"),
        sa.CheckConstraint("status = 'staff_attested_unverified'", name="ck_catinat_batch_unverified"),
        sa.CheckConstraint("length(source_hash) = 64", name="ck_catinat_batch_source_hash"),
    )
    op.create_index("ix_catinat_batch_day", "inventory_catinat_batch_evidence",
                    ["tenant_id", "business_date", "sheet_name", "cell_ref"])
    op.drop_constraint("ck_catinat_approval_kind", "inventory_catinat_approval_requests", type_="check")
    op.create_check_constraint("ck_catinat_approval_kind", "inventory_catinat_approval_requests", NEW_KINDS)


def downgrade():
    # A downgrade cannot discard approved or pending disposal workflow evidence.
    conn = op.get_bind()
    has_waste_requests = conn.scalar(sa.text(
        "SELECT COUNT(*) FROM inventory_catinat_approval_requests WHERE kind = 'waste_disposal'"
    ))
    if has_waste_requests:
        raise RuntimeError("waste_disposal_requests_prevent_downgrade")
    if conn.scalar(sa.text("SELECT COUNT(*) FROM inventory_catinat_batch_evidence")):
        raise RuntimeError("batch_evidence_prevents_downgrade")
    op.drop_constraint("ck_catinat_approval_kind", "inventory_catinat_approval_requests", type_="check")
    op.create_check_constraint("ck_catinat_approval_kind", "inventory_catinat_approval_requests", OLD_KINDS)
    op.drop_index("ix_catinat_batch_day", table_name="inventory_catinat_batch_evidence")
    op.drop_table("inventory_catinat_batch_evidence")
