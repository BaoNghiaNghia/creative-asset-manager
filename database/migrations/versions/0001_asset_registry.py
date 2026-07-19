"""Create tenant-scoped asset registry.

Revision ID: 0001_asset_registry
Revises: none

Rollback: downgrade drops only the five Step 03 registry tables. Export or
back up registry data before downgrade after the feature has been enabled.
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_asset_registry"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "source_key", name="uq_external_sources_tenant_key"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_external_sources_tenant_id"),
    )
    op.create_index("ix_external_sources_tenant_type", "external_sources", ["tenant_id", "source_type"])

    op.create_table(
        "source_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("external_source_id", sa.String(36), nullable=False),
        sa.Column("external_asset_id", sa.String(2048), nullable=False),
        sa.Column("filename", sa.String(1024)),
        sa.Column("mime_type", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("source_created_at", sa.DateTime(timezone=True)),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
        sa.Column("provider_checksum", sa.String(255)),
        sa.Column("provider_version", sa.String(255)),
        sa.Column("hashed_provider_checksum", sa.String(255)),
        sa.Column("hashed_provider_version", sa.String(255)),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE",
            name="fk_source_assets_tenant_source",
        ),
        sa.UniqueConstraint(
            "tenant_id", "external_source_id", "external_asset_id",
            name="uq_source_assets_source_identity",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_source_assets_tenant_id"),
    )
    op.create_index("ix_source_assets_tenant_deleted", "source_assets", ["tenant_id", "deleted_at"])

    op.create_table(
        "assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("analysis_image_hash", sa.String(64)),
        sa.Column("mime_type", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "content_hash", name="uq_assets_tenant_content_hash"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_assets_tenant_id"),
    )
    op.create_index("ix_assets_content_hash", "assets", ["content_hash"])

    op.create_table(
        "asset_source_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("source_asset_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"],
            ondelete="CASCADE", name="fk_asset_source_links_tenant_asset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_asset_id"],
            ["source_assets.tenant_id", "source_assets.id"],
            ondelete="CASCADE", name="fk_asset_source_links_tenant_source_asset",
        ),
        sa.UniqueConstraint("asset_id", "source_asset_id", name="uq_asset_source_links_pair"),
    )
    op.create_index("ix_asset_source_links_source_asset", "asset_source_links", ["source_asset_id"])

    op.create_table(
        "source_sync_cursors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("external_source_id", sa.String(36), nullable=False),
        sa.Column("cursor_key", sa.String(100), nullable=False),
        sa.Column("cursor_value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE", name="fk_source_sync_cursors_tenant_source",
        ),
        sa.UniqueConstraint(
            "tenant_id", "external_source_id", "cursor_key",
            name="uq_source_sync_cursors_source_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("source_sync_cursors")
    op.drop_index("ix_asset_source_links_source_asset", table_name="asset_source_links")
    op.drop_table("asset_source_links")
    op.drop_index("ix_assets_content_hash", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_source_assets_tenant_deleted", table_name="source_assets")
    op.drop_table("source_assets")
    op.drop_index("ix_external_sources_tenant_type", table_name="external_sources")
    op.drop_table("external_sources")
