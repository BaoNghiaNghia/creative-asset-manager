"""Add Phase 7 Inventory transaction document semantics.

Revision ID: 0038_inventory_txn
Revises: 0037_inventory_normalize_review
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_inventory_txn"
down_revision = "0037_inventory_normalize_review"
branch_labels = None
depends_on = None

_PREVIOUS_DOCUMENT_TYPES = "document_type IN ('stock_count','warehouse_transfer','waste','unclassified')"
_PHASE_SEVEN_DOCUMENT_TYPES = "document_type IN ('opening','receipt','stock_count','warehouse_transfer','waste','unclassified')"


def upgrade():
    with op.batch_alter_table("inventory_documents") as batch_op:
        batch_op.add_column(sa.Column("destination_location_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_inventory_documents_tenant_destination_location",
            "inventory_locations",
            ["tenant_id", "destination_location_id"],
            ["tenant_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.drop_constraint("ck_inventory_documents_type", type_="check")
        batch_op.create_check_constraint("ck_inventory_documents_type", _PHASE_SEVEN_DOCUMENT_TYPES)


def downgrade():
    # Phase 6 did not recognize Phase 7-only document kinds.  Preserve the
    # records while making them valid for its constrained representation.
    op.execute(
        "UPDATE inventory_documents SET document_type = 'unclassified' "
        "WHERE document_type IN ('opening', 'receipt')"
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # IF EXISTS keeps the migration recoverable for databases created by
        # the early Phase 7 draft, before this tenant-safe FK was added.
        op.execute(
            "ALTER TABLE inventory_documents DROP CONSTRAINT IF EXISTS "
            "fk_inventory_documents_tenant_destination_location"
        )
    with op.batch_alter_table("inventory_documents") as batch_op:
        if bind.dialect.name != "postgresql":
            batch_op.drop_constraint(
                "fk_inventory_documents_tenant_destination_location",
                type_="foreignkey",
            )
        batch_op.drop_constraint("ck_inventory_documents_type", type_="check")
        batch_op.create_check_constraint("ck_inventory_documents_type", _PREVIOUS_DOCUMENT_TYPES)
        batch_op.drop_column("destination_location_id")