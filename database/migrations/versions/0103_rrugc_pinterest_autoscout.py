"""Add RRUGC Pinterest Auto Scout agents and durable scan leases.

Revision ID: 0103_rrugc_pinterest_autoscout
Revises: 0102_rrugc_delivery_automation
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0103_rrugc_pinterest_autoscout"
down_revision = "0102_rrugc_delivery_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_campaigns",
        sa.Column("auto_scout", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column(
            "scan_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_next_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_lease_agent_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_lease_run_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_last_started_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_last_completed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_empty_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("scan_last_error_code", sa.String(length=100)),
    )
    op.create_index(
        "ix_rrugc_campaign_autoscout_due",
        "rrugc_campaigns",
        ["tenant_id", "status", "auto_scout", "scan_next_at"],
    )

    op.create_table(
        "rrugc_scout_agents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="offline",
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("client_version", sa.String(length=64)),
        sa.Column("machine_label", sa.String(length=160)),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_rrugc_scout_agent_tenant_id",
        ),
    )
    op.create_index(
        "ix_rrugc_scout_agent_tenant_active",
        "rrugc_scout_agents",
        ["tenant_id", "active", "last_seen_at"],
    )

    op.create_table(
        "rrugc_scout_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="claimed",
        ),
        sa.Column("query", sa.String(length=500), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("max_scroll_batches", sa.Integer(), nullable=False),
        sa.Column("auto_import", sa.Boolean(), nullable=False),
        sa.Column("progress_before", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("existing_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_scout_run_campaign",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["rrugc_scout_agents.tenant_id", "rrugc_scout_agents.id"],
            name="fk_rrugc_scout_run_agent",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_rrugc_scout_run_campaign_created",
        "rrugc_scout_runs",
        ["tenant_id", "campaign_id", "created_at"],
    )
    op.create_index(
        "ix_rrugc_scout_run_agent_status",
        "rrugc_scout_runs",
        ["tenant_id", "agent_id", "status", "updated_at"],
    )

    op.alter_column("rrugc_campaigns", "auto_scout", server_default=None)
    op.alter_column("rrugc_campaigns", "scan_interval_seconds", server_default=None)
    op.alter_column("rrugc_campaigns", "scan_attempt_count", server_default=None)
    op.alter_column("rrugc_campaigns", "scan_empty_streak", server_default=None)
    op.alter_column("rrugc_scout_agents", "status", server_default=None)
    op.alter_column("rrugc_scout_agents", "active", server_default=None)
    op.alter_column("rrugc_scout_runs", "status", server_default=None)
    op.alter_column("rrugc_scout_runs", "progress_before", server_default=None)
    op.alter_column("rrugc_scout_runs", "submitted_count", server_default=None)
    op.alter_column("rrugc_scout_runs", "created_count", server_default=None)
    op.alter_column("rrugc_scout_runs", "existing_count", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_scout_run_agent_status",
        table_name="rrugc_scout_runs",
    )
    op.drop_index(
        "ix_rrugc_scout_run_campaign_created",
        table_name="rrugc_scout_runs",
    )
    op.drop_table("rrugc_scout_runs")
    op.drop_index(
        "ix_rrugc_scout_agent_tenant_active",
        table_name="rrugc_scout_agents",
    )
    op.drop_table("rrugc_scout_agents")
    op.drop_index(
        "ix_rrugc_campaign_autoscout_due",
        table_name="rrugc_campaigns",
    )
    op.drop_column("rrugc_campaigns", "scan_last_error_code")
    op.drop_column("rrugc_campaigns", "scan_empty_streak")
    op.drop_column("rrugc_campaigns", "scan_attempt_count")
    op.drop_column("rrugc_campaigns", "scan_last_completed_at")
    op.drop_column("rrugc_campaigns", "scan_last_started_at")
    op.drop_column("rrugc_campaigns", "scan_lease_expires_at")
    op.drop_column("rrugc_campaigns", "scan_lease_run_id")
    op.drop_column("rrugc_campaigns", "scan_lease_agent_id")
    op.drop_column("rrugc_campaigns", "scan_next_at")
    op.drop_column("rrugc_campaigns", "scan_interval_seconds")
    op.drop_column("rrugc_campaigns", "auto_scout")
