"""Create managed asset storage registry.

Revision ID: 0003_managed_asset_storage
Revises: 0002_processing_jobs_outbox

Rollback: stop asset_store workers, export remote IDs if needed, then downgrade.
Remote Google Drive files are not deleted by the database downgrade.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_managed_asset_storage"
down_revision = "0002_processing_jobs_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_storage_objects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_provider", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("remote_file_id", sa.String(255)),
        sa.Column("remote_folder_id", sa.String(255)),
        sa.Column("web_url", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stored_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["assets.tenant_id", "assets.id"],
            ondelete="CASCADE",
            name="fk_asset_storage_objects_tenant_asset",
        ),
        sa.UniqueConstraint(
            "tenant_id", "asset_id", "storage_provider",
            name="uq_asset_storage_objects_asset_provider",
        ),
        sa.UniqueConstraint(
            "storage_provider", "remote_file_id",
            name="uq_asset_storage_objects_remote_file",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'uploading', 'stored', 'retry', 'failed')",
            name="ck_asset_storage_objects_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_asset_storage_objects_attempt_count"),
    )
    op.create_index(
        "ix_asset_storage_objects_status",
        "asset_storage_objects",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_storage_objects_status", table_name="asset_storage_objects")
    op.drop_table("asset_storage_objects")
