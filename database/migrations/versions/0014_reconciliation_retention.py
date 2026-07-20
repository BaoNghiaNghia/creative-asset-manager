"""Reconciliation generations and retention cleanup state.

Revision ID: 0014_reconciliation_retention
Revises: 0013_persistent_oauth_sessions

Rollback removes operational run history and encrypted transient URL columns.
Disable retention cleanup and drain source sync jobs before downgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_reconciliation_retention"
down_revision = "0013_persistent_oauth_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("source_assets", sa.Column("last_seen_generation", sa.BigInteger()))
    op.add_column("source_assets", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_source_assets_reconciliation_generation",
        "source_assets",
        ["tenant_id", "external_source_id", "last_seen_generation", "deleted_at"],
    )
    op.create_table(
        "source_sync_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("external_source_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("checkpoint_cursor", sa.Text()),
        sa.Column("pages_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_seen_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_json", sa.JSON()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE",
            name="fk_source_sync_runs_tenant_source",
        ),
        sa.UniqueConstraint("tenant_id", "external_source_id", "generation", name="uq_source_sync_runs_generation"),
        sa.CheckConstraint("mode IN ('full', 'incremental')", name="ck_source_sync_runs_mode"),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed', 'cancelled')", name="ck_source_sync_runs_status"),
    )
    op.create_index("ix_source_sync_runs_source_status", "source_sync_runs", ["tenant_id", "external_source_id", "status"])
    op.create_index(
        "uq_source_sync_runs_active_full", "source_sync_runs",
        ["tenant_id", "external_source_id"], unique=True,
        postgresql_where=sa.text("mode = 'full' AND status = 'running'"),
        sqlite_where=sa.text("mode = 'full' AND status = 'running'"),
    )

    with op.batch_alter_table("asset_ingestion_items") as batch:
        batch.alter_column("download_url", existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column("download_url_ciphertext", sa.Text()))
        batch.add_column(sa.Column("download_url_key_version", sa.String(64)))
        batch.add_column(sa.Column("download_url_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("download_url_consumed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("download_url_redacted_at", sa.DateTime(timezone=True)))

    # Existing plaintext signed URLs cannot be safely encrypted without access
    # to application keys. Tombstone them and require a fresh ingestion attempt.
    op.execute(
        "UPDATE asset_ingestion_items "
        "SET download_url = NULL, download_url_redacted_at = CURRENT_TIMESTAMP "
        "WHERE download_url IS NOT NULL"
    )
    op.execute("UPDATE asset_ingestions SET request_json = '{}'")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "UPDATE processing_jobs SET payload_json = (payload_json::jsonb - 'download_url')::json "
            "WHERE entity_type = 'asset_ingestion_item'"
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            "UPDATE processing_jobs SET payload_json = json_remove(payload_json, '$.download_url') "
            "WHERE entity_type = 'asset_ingestion_item'"
        )

    op.create_table(
        "retention_cleanup_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("policy_name", sa.String(100), nullable=False),
        sa.Column("record_types_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_json", sa.JSON(), nullable=False),
        sa.Column("counts_json", sa.JSON(), nullable=False),
        sa.Column("max_rows", sa.Integer(), nullable=False),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("error_json", sa.JSON()),
        sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'cancelled')", name="ck_retention_cleanup_runs_status"),
        sa.CheckConstraint("max_rows > 0", name="ck_retention_cleanup_runs_max_rows"),
    )
    op.create_index("ix_retention_cleanup_runs_tenant_status", "retention_cleanup_runs", ["tenant_id", "status"])
    op.create_index(
        "uq_retention_cleanup_runs_active_scope", "retention_cleanup_runs",
        ["tenant_id", "policy_name"], unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
        sqlite_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade():
    op.drop_index("uq_retention_cleanup_runs_active_scope", table_name="retention_cleanup_runs")
    op.drop_index("ix_retention_cleanup_runs_tenant_status", table_name="retention_cleanup_runs")
    op.drop_table("retention_cleanup_runs")
    # Ciphertext cannot be decrypted by a schema migration. Preserve the old
    # non-null contract with an explicit tombstone for already-redacted rows.
    op.execute(
        "UPDATE asset_ingestion_items "
        "SET download_url = 'https://redacted.invalid/' "
        "WHERE download_url IS NULL"
    )
    with op.batch_alter_table("asset_ingestion_items") as batch:
        batch.drop_column("download_url_redacted_at")
        batch.drop_column("download_url_consumed_at")
        batch.drop_column("download_url_expires_at")
        batch.drop_column("download_url_key_version")
        batch.drop_column("download_url_ciphertext")
        batch.alter_column("download_url", existing_type=sa.Text(), nullable=False)
    op.drop_index("uq_source_sync_runs_active_full", table_name="source_sync_runs")
    op.drop_index("ix_source_sync_runs_source_status", table_name="source_sync_runs")
    op.drop_table("source_sync_runs")
    op.drop_index("ix_source_assets_reconciliation_generation", table_name="source_assets")
    op.drop_column("source_assets", "last_seen_at")
    op.drop_column("source_assets", "last_seen_generation")
