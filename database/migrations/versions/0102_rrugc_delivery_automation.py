"""Add RRUGC delivery automation retry state and operational events.

Revision ID: 0102_rrugc_delivery_automation
Revises: 0101_rrugc_delivery_lifecycle
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0102_rrugc_delivery_automation"
down_revision = "0101_rrugc_delivery_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_delivery_packages",
        sa.Column("auto_retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rrugc_delivery_packages",
        sa.Column("last_retry_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_delivery_packages",
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_rrugc_delivery_package_retry_due",
        "rrugc_delivery_packages",
        ["tenant_id", "status", "next_retry_at"],
    )

    op.create_table(
        "rrugc_delivery_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36)),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("payload_json", sa.JSON()),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_delivery_event_campaign",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["rrugc_delivery_packages.id"],
            name="fk_rrugc_delivery_event_package",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_rrugc_delivery_event_key",
        ),
    )
    op.create_index(
        "ix_rrugc_delivery_event_tenant_created",
        "rrugc_delivery_events",
        ["tenant_id", "created_at"],
    )

    op.alter_column(
        "rrugc_delivery_packages",
        "auto_retry_count",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_delivery_event_tenant_created",
        table_name="rrugc_delivery_events",
    )
    op.drop_table("rrugc_delivery_events")
    op.drop_index(
        "ix_rrugc_delivery_package_retry_due",
        table_name="rrugc_delivery_packages",
    )
    op.drop_column("rrugc_delivery_packages", "next_retry_at")
    op.drop_column("rrugc_delivery_packages", "last_retry_at")
    op.drop_column("rrugc_delivery_packages", "auto_retry_count")
