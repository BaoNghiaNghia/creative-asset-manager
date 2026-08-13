"""Persist Inventory export retry and archive state.

Revision ID: 0040_inventory_export_archive
Revises: 0039_inventory_daily_run
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_inventory_export_archive"
down_revision = "0039_inventory_daily_run"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inventory_settings") as batch_op:
        batch_op.add_column(sa.Column("old_image_archive_folder_id", sa.String(2048), nullable=True))
    with op.batch_alter_table("inventory_exports") as batch_op:
        batch_op.add_column(sa.Column("backup_drive_file_id", sa.String(2048), nullable=True))
        batch_op.add_column(
            sa.Column(
                "archive_status", sa.String(32), nullable=False,
                server_default="not_requested",
            )
        )
        batch_op.add_column(sa.Column("archive_error_code", sa.String(100), nullable=True))
        batch_op.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("inventory_exports") as batch_op:
        batch_op.drop_column("started_at")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("archive_error_code")
        batch_op.drop_column("archive_status")
        batch_op.drop_column("backup_drive_file_id")
    with op.batch_alter_table("inventory_settings") as batch_op:
        batch_op.drop_column("old_image_archive_folder_id")