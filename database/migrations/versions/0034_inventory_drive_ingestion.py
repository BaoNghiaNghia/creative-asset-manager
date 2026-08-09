"""Add Inventory Drive ingestion state required by Phase 3.

Revision ID: 0034_inventory_drive_ingestion
Revises: a7dd7ccdbf1a
"""

from alembic import op
import sqlalchemy as sa


revision = "0034_inventory_drive_ingestion"
down_revision = "a7dd7ccdbf1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE inventory_settings
        SET drive_poll_interval_seconds = CASE
            WHEN drive_poll_interval_seconds < 60 THEN 60
            WHEN drive_poll_interval_seconds > 300 THEN 300
            ELSE drive_poll_interval_seconds
        END"""
    )
    with op.batch_alter_table("inventory_settings") as batch:
        batch.drop_constraint(
            "ck_inventory_settings_poll_interval",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_inventory_settings_poll_interval",
            "drive_poll_interval_seconds >= 60 AND drive_poll_interval_seconds <= 300",
        )
        batch.add_column(
            sa.Column("last_successful_poll_at", sa.DateTime(timezone=True))
        )
        batch.add_column(sa.Column("last_poll_error_code", sa.String(length=100)))
        batch.add_column(sa.Column("last_poll_error_message", sa.Text()))

    with op.batch_alter_table("inventory_source_files") as batch:
        batch.drop_constraint(
            "ck_inventory_source_files_status",
            type_="check",
        )
        batch.add_column(sa.Column("storage_key", sa.String(length=1024)))
        batch.add_column(
            sa.Column("duplicate_of_source_file_id", sa.String(length=36))
        )
        batch.add_column(sa.Column("last_error_code", sa.String(length=100)))
        batch.add_column(sa.Column("last_error_message", sa.Text()))
        batch.create_check_constraint(
            "ck_inventory_source_files_status",
            "status IN ('discovered','queued','downloading','downloaded','duplicate','unsupported','retryable_failure','terminal_failure')",
        )
        batch.create_foreign_key(
            "fk_inventory_source_files_tenant_duplicate",
            "inventory_source_files",
            ["tenant_id", "duplicate_of_source_file_id"],
            ["tenant_id", "id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    op.execute(
        """UPDATE inventory_source_files
        SET status = CASE
            WHEN status IN ('queued','downloading','retryable_failure') THEN 'discovered'
            WHEN status = 'duplicate' THEN 'processed'
            WHEN status = 'unsupported' THEN 'ignored'
            WHEN status = 'terminal_failure' THEN 'failed'
            ELSE status
        END"""
    )
    with op.batch_alter_table("inventory_source_files") as batch:
        batch.drop_constraint(
            "fk_inventory_source_files_tenant_duplicate",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "ck_inventory_source_files_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_inventory_source_files_status",
            "status IN ('discovered','downloaded','processing','processed','ignored','failed')",
        )
        batch.drop_column("last_error_message")
        batch.drop_column("last_error_code")
        batch.drop_column("duplicate_of_source_file_id")
        batch.drop_column("storage_key")

    with op.batch_alter_table("inventory_settings") as batch:
        batch.drop_column("last_poll_error_message")
        batch.drop_column("last_poll_error_code")
        batch.drop_column("last_successful_poll_at")
        batch.drop_constraint(
            "ck_inventory_settings_poll_interval",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_inventory_settings_poll_interval",
            "drive_poll_interval_seconds > 0",
        )
