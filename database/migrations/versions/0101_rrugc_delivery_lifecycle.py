"""Add RRUGC delivery destinations, packages, and lifecycle policy.

Revision ID: 0101_rrugc_delivery_lifecycle
Revises: 0100_rrugc_export_catalog
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0101_rrugc_delivery_lifecycle"
down_revision = "0100_rrugc_export_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_campaigns",
        sa.Column(
            "auto_complete_on_delivery",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("completion_destination_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "rrugc_delivery_destinations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="google_drive_folder",
        ),
        sa.Column("target_ref", sa.String(length=255), nullable=False),
        sa.Column(
            "retention_days",
            sa.Integer(),
            nullable=False,
            server_default="90",
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_rrugc_delivery_destination_name",
        ),
    )
    op.create_index(
        "ix_rrugc_delivery_destination_tenant_active",
        "rrugc_delivery_destinations",
        ["tenant_id", "active", "updated_at"],
    )

    op.create_table(
        "rrugc_delivery_packages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("destination_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "export_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "delivered_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "failed_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("manifest_json", sa.JSON()),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_delivery_package_campaign",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["rrugc_delivery_destinations.id"],
            name="fk_rrugc_delivery_package_destination",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_rrugc_delivery_package_key",
        ),
    )
    op.create_index(
        "ix_rrugc_delivery_package_campaign_status",
        "rrugc_delivery_packages",
        ["tenant_id", "campaign_id", "status", "created_at"],
    )
    op.create_index(
        "ix_rrugc_delivery_package_expires",
        "rrugc_delivery_packages",
        ["tenant_id", "status", "expires_at"],
    )

    op.create_table(
        "rrugc_delivery_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("export_id", sa.String(length=36), nullable=False),
        sa.Column("catalog_asset_id", sa.String(length=36), nullable=False),
        sa.Column("source_remote_file_id", sa.String(length=255), nullable=False),
        sa.Column("delivered_remote_file_id", sa.String(length=255)),
        sa.Column("delivered_web_url", sa.Text()),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["rrugc_delivery_packages.id"],
            name="fk_rrugc_delivery_item_package",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["export_id"],
            ["rrugc_exports.id"],
            name="fk_rrugc_delivery_item_export",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "catalog_asset_id"],
            ["assets.tenant_id", "assets.id"],
            name="fk_rrugc_delivery_item_catalog_asset",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "package_id",
            "export_id",
            name="uq_rrugc_delivery_item_export",
        ),
    )
    op.create_index(
        "ix_rrugc_delivery_item_package_status",
        "rrugc_delivery_items",
        ["tenant_id", "package_id", "status"],
    )

    op.alter_column(
        "rrugc_campaigns",
        "auto_complete_on_delivery",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_delivery_item_package_status",
        table_name="rrugc_delivery_items",
    )
    op.drop_table("rrugc_delivery_items")
    op.drop_index(
        "ix_rrugc_delivery_package_expires",
        table_name="rrugc_delivery_packages",
    )
    op.drop_index(
        "ix_rrugc_delivery_package_campaign_status",
        table_name="rrugc_delivery_packages",
    )
    op.drop_table("rrugc_delivery_packages")
    op.drop_index(
        "ix_rrugc_delivery_destination_tenant_active",
        table_name="rrugc_delivery_destinations",
    )
    op.drop_table("rrugc_delivery_destinations")
    op.drop_column("rrugc_campaigns", "completed_at")
    op.drop_column("rrugc_campaigns", "completion_destination_id")
    op.drop_column("rrugc_campaigns", "auto_complete_on_delivery")
