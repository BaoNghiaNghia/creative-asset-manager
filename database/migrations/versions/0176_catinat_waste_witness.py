"""Catinat independent physical-waste witness and manager countersign; no ledger.

Revision ID: 0176_catinat_waste_witness
Revises: 0175_catinat_batch_evidence
"""
from alembic import op
import sqlalchemy as sa

revision = "0176_catinat_waste_witness"
down_revision = "0175_catinat_batch_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_catinat_waste_witnesses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("approval_request_id", sa.String(36), nullable=False),
        sa.Column("batch_evidence_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(100), nullable=False),
        sa.Column("evidence_reference", sa.String(2048), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("witnessed_by", sa.String(255), nullable=False),
        sa.Column("witnessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["inventory_catinat_approval_requests.tenant_id", "inventory_catinat_approval_requests.id"],
            name="fk_catinat_waste_witness_approval", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_evidence_id"],
            ["inventory_catinat_batch_evidence.tenant_id", "inventory_catinat_batch_evidence.id"],
            name="fk_catinat_waste_witness_batch", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_catinat_waste_witness_tenant_id"),
        sa.UniqueConstraint("tenant_id", "approval_request_id", name="uq_catinat_waste_witness_approval"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_catinat_waste_witness_key"),
        sa.CheckConstraint("quantity > 0", name="ck_catinat_waste_witness_quantity"),
        sa.CheckConstraint("length(source_hash) = 64", name="ck_catinat_waste_witness_hash"),
        sa.CheckConstraint("status = 'staff_witnessed_unposted'", name="ck_catinat_waste_witness_unposted"),
    )
    op.create_index("ix_catinat_waste_witness_tenant_day",
                    "inventory_catinat_waste_witnesses", ["tenant_id", "business_date"])
    op.create_table(
        "inventory_catinat_waste_certifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("witness_id", sa.String(36), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("approved_source_hash", sa.String(64), nullable=False),
        sa.Column("certified_by", sa.String(255), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "witness_id"],
            ["inventory_catinat_waste_witnesses.tenant_id", "inventory_catinat_waste_witnesses.id"],
            name="fk_catinat_waste_cert_witness", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "witness_id", name="uq_catinat_waste_cert_witness"),
        sa.CheckConstraint("status = 'countersigned_unposted'", name="ck_catinat_waste_cert_unposted"),
    )
    op.create_index("ix_catinat_waste_cert_tenant_day",
                    "inventory_catinat_waste_certifications", ["tenant_id", "business_date"])


def downgrade():
    # Records are immutable evidence and must never be removed by release rollback.
    bind = op.get_bind()
    for name in ("inventory_catinat_waste_certifications", "inventory_catinat_waste_witnesses"):
        if bind.scalar(sa.text(f"SELECT COUNT(*) FROM {name}")):
            raise RuntimeError("catinat_waste_evidence_prevents_downgrade")
    op.drop_index("ix_catinat_waste_cert_tenant_day",
                  table_name="inventory_catinat_waste_certifications")
    op.drop_table("inventory_catinat_waste_certifications")
    op.drop_index("ix_catinat_waste_witness_tenant_day",
                  table_name="inventory_catinat_waste_witnesses")
    op.drop_table("inventory_catinat_waste_witnesses")
