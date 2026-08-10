"""Add isolated Inventory document preparation state.

Revision ID: 0035_inventory_document_prep
Revises: 0034_inventory_drive_ingestion
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_inventory_document_prep"
down_revision = "0034_inventory_drive_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_source_files") as batch:
        batch.add_column(sa.Column("preparation_status", sa.String(length=24), server_default="not_requested", nullable=False))
        batch.add_column(sa.Column("preparation_version", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("preparation_error_code", sa.String(length=100)))
        batch.add_column(sa.Column("preparation_error_message", sa.Text()))

    with op.batch_alter_table("inventory_documents") as batch:
        batch.drop_constraint("ck_inventory_documents_type", type_="check")
        batch.drop_constraint("ck_inventory_documents_status", type_="check")
        batch.alter_column("business_date", existing_type=sa.Date(), nullable=True)
        batch.alter_column("location_id", existing_type=sa.String(length=36), nullable=True)
        batch.create_check_constraint("ck_inventory_documents_type", "document_type IN ('stock_count','warehouse_transfer','waste','unclassified')")
        batch.create_check_constraint("ck_inventory_documents_status", "status IN ('collecting','preparing','prepared','duplicate','retryable_failure','terminal_failure','analyzing','needs_review','approved','rejected','finalized')")

    with op.batch_alter_table("inventory_document_pages") as batch:
        batch.add_column(sa.Column("preparation_version", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("preparation_status", sa.String(length=24), server_default="queued", nullable=False))
        batch.add_column(sa.Column("prepared_storage_key", sa.String(length=1024)))
        batch.add_column(sa.Column("prepared_content_sha256", sa.String(length=64)))
        batch.add_column(sa.Column("prepared_size_bytes", sa.BigInteger()))
        batch.add_column(sa.Column("prepared_mime_type", sa.String(length=255)))
        batch.add_column(sa.Column("image_width", sa.Integer()))
        batch.add_column(sa.Column("image_height", sa.Integer()))
        batch.add_column(sa.Column("preparation_error_code", sa.String(length=100)))
        batch.add_column(sa.Column("preparation_error_message", sa.Text()))
        batch.create_check_constraint("ck_inventory_pages_preparation_status", "preparation_status IN ('queued','preparing','prepared','duplicate','retryable_failure','terminal_failure')")


def downgrade() -> None:
    with op.batch_alter_table("inventory_document_pages") as batch:
        batch.drop_constraint("ck_inventory_pages_preparation_status", type_="check")
        for column in ("preparation_error_message", "preparation_error_code", "image_height", "image_width", "prepared_mime_type", "prepared_size_bytes", "prepared_content_sha256", "prepared_storage_key", "preparation_status", "preparation_version"):
            batch.drop_column(column)

    with op.batch_alter_table("inventory_documents") as batch:
        batch.drop_constraint("ck_inventory_documents_status", type_="check")
        batch.drop_constraint("ck_inventory_documents_type", type_="check")
        batch.alter_column("location_id", existing_type=sa.String(length=36), nullable=False)
        batch.alter_column("business_date", existing_type=sa.Date(), nullable=False)
        batch.create_check_constraint("ck_inventory_documents_type", "document_type IN ('stock_count','warehouse_transfer','waste')")
        batch.create_check_constraint("ck_inventory_documents_status", "status IN ('collecting','analyzing','needs_review','approved','rejected','finalized')")

    with op.batch_alter_table("inventory_source_files") as batch:
        batch.drop_column("preparation_error_message")
        batch.drop_column("preparation_error_code")
        batch.drop_column("preparation_version")
        batch.drop_column("preparation_status")
