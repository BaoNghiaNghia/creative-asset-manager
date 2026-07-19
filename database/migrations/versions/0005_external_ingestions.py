"""Create authenticated asynchronous external ingestion records.

Revision ID: 0005_external_ingestions
Revises: 0004_dynamic_ai_metadata

Rollback: stop external ingestion callers, preserve/export ingestion audit
records if required, then downgrade. Processing jobs remain for operational
inspection and may reference removed ingestion item IDs in their JSON payload.
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_external_ingestions"
down_revision = "0004_dynamic_ai_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_api_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("external_source_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE",
            name="fk_external_api_credentials_tenant_source",
        ),
        sa.UniqueConstraint("secret_hash", name="uq_external_api_credentials_secret_hash"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_external_api_credentials_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "external_source_id",
            name="uq_external_api_credentials_tenant_id_source",
        ),
        sa.CheckConstraint("rate_limit_per_minute > 0", name="ck_external_api_credentials_rate_limit"),
    )
    op.create_index(
        "ix_external_api_credentials_source",
        "external_api_credentials",
        ["tenant_id", "external_source_id", "active"],
    )
    op.create_table(
        "external_api_rate_limits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("credential_id", sa.String(36), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["external_api_credentials.id"],
            ondelete="CASCADE",
            name="fk_external_api_rate_limits_credential",
        ),
        sa.UniqueConstraint(
            "credential_id", "window_start", name="uq_external_api_rate_limits_window"
        ),
        sa.CheckConstraint("request_count > 0", name="ck_external_api_rate_limits_count"),
    )
    op.create_index(
        "ix_external_api_rate_limits_window",
        "external_api_rate_limits",
        ["window_start"],
    )
    op.create_table(
        "asset_ingestions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("external_source_id", sa.String(36), nullable=False),
        sa.Column("credential_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("received_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="RESTRICT",
            name="fk_asset_ingestions_tenant_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id", "external_source_id"],
            ["external_api_credentials.tenant_id", "external_api_credentials.id", "external_api_credentials.external_source_id"],
            ondelete="RESTRICT",
            name="fk_asset_ingestions_tenant_credential",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "external_source_id",
            "idempotency_key",
            name="uq_asset_ingestions_tenant_source_key",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_asset_ingestions_tenant_id"),
        sa.CheckConstraint(
            "status IN ('accepted', 'processing', 'completed', 'partial_failed', 'failed')",
            name="ck_asset_ingestions_status",
        ),
        sa.CheckConstraint("received_count > 0", name="ck_asset_ingestions_received_count"),
    )
    op.create_index(
        "ix_asset_ingestions_source_created",
        "asset_ingestions",
        ["tenant_id", "external_source_id", "created_at"],
    )
    op.create_table(
        "asset_ingestion_items",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("ingestion_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("external_asset_id", sa.String(2048), nullable=False),
        sa.Column("download_url", sa.Text(), nullable=False),
        sa.Column("provider_checksum", sa.String(255)),
        sa.Column("filename", sa.String(1024)),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("processing_job_id", sa.String(36)),
        sa.Column("source_asset_id", sa.String(36)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ingestion_id"],
            ["asset_ingestions.tenant_id", "asset_ingestions.id"],
            ondelete="CASCADE",
            name="fk_asset_ingestion_items_tenant_ingestion",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_asset_id"],
            ["source_assets.tenant_id", "source_assets.id"],
            ondelete="RESTRICT",
            name="fk_asset_ingestion_items_tenant_source_asset",
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id"],
            ["processing_jobs.id"],
            ondelete="SET NULL",
            name="fk_asset_ingestion_items_processing_job",
        ),
        sa.UniqueConstraint(
            "ingestion_id", "position", name="uq_asset_ingestion_items_position"
        ),
        sa.UniqueConstraint(
            "ingestion_id",
            "external_asset_id",
            name="uq_asset_ingestion_items_external_id",
        ),
        sa.CheckConstraint("position >= 0", name="ck_asset_ingestion_items_position"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_asset_ingestion_items_status",
        ),
    )
    op.create_index(
        "ix_asset_ingestion_items_status",
        "asset_ingestion_items",
        ["tenant_id", "ingestion_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_ingestion_items_status", table_name="asset_ingestion_items")
    op.drop_table("asset_ingestion_items")
    op.drop_index("ix_asset_ingestions_source_created", table_name="asset_ingestions")
    op.drop_table("asset_ingestions")
    op.drop_index("ix_external_api_rate_limits_window", table_name="external_api_rate_limits")
    op.drop_table("external_api_rate_limits")
    op.drop_index("ix_external_api_credentials_source", table_name="external_api_credentials")
    op.drop_table("external_api_credentials")
