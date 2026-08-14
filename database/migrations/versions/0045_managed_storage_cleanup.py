"""Index managed storage cleanup candidates.

Revision ID: 0045_managed_storage_cleanup
Revises: 0044_folder_notes
"""
from alembic import op

revision = "0045_managed_storage_cleanup"
down_revision = "0044_folder_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_asset_storage_objects_cleanup",
        "asset_storage_objects",
        ["tenant_id", "status", "stored_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_storage_objects_cleanup", table_name="asset_storage_objects")
