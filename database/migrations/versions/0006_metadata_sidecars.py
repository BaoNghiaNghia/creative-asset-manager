"""Create independent Google Drive metadata sidecar export state.

Revision ID: 0006_metadata_sidecars
Revises: 0005_external_ingestions

Rollback: disable DRIVE_METADATA_SIDECAR_ENABLED, stop sidecar workers, export
audit records if required, then downgrade. Remote Google Drive JSON files are
exports and are intentionally not deleted by this migration.
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_metadata_sidecars"
down_revision = "0005_external_ingestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metadata_sidecar_exports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("storage_provider", sa.String(64), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("remote_file_id", sa.String(255)),
        sa.Column("remote_folder_id", sa.String(255)),
        sa.Column("storage_key", sa.String(512)),
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
            name="fk_metadata_sidecars_tenant_asset",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["asset_ai_analyses.id"],
            ondelete="CASCADE",
            name="fk_metadata_sidecars_analysis",
        ),
        sa.UniqueConstraint(
            "analysis_id",
            "storage_provider",
            name="uq_metadata_sidecars_analysis_provider",
        ),
        sa.UniqueConstraint(
            "storage_provider",
            "remote_file_id",
            name="uq_metadata_sidecars_remote_file",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'exporting', 'stored', 'retry', 'failed')",
            name="ck_metadata_sidecars_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_metadata_sidecars_attempt_count",
        ),
    )
    op.create_index(
        "ix_metadata_sidecars_status",
        "metadata_sidecar_exports",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_sidecars_status", table_name="metadata_sidecar_exports")
    op.drop_table("metadata_sidecar_exports")
