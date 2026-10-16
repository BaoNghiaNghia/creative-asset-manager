"""Add narrow worker lifecycle receipts, no scan dispatch.

Revision ID: 0161_branding_dna_worker_events
Revises: 0160_branding_dna_scan_tasks
"""
from alembic import op
import sqlalchemy as sa

revision = "0161_branding_dna_worker_events"
down_revision = "0160_branding_dna_scan_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bc_worker_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("bc_browser_agents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("detail_code", sa.String(32), nullable=False),
        sa.Column("event_sha256", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "agent_id", "event_id", name="uq_bc_worker_event_replay"),
        sa.CheckConstraint(
            "kind IN ('started','stopped','heartbeat_failure','heartbeat_recovered')",
            name="ck_bc_worker_event_kind",
        ),
    )
    op.create_index("ix_bc_worker_events_tenant_received", "bc_worker_events", ["tenant_id", "received_at"])
    op.create_index("ix_bc_worker_events_agent_received", "bc_worker_events", ["tenant_id", "agent_id", "received_at"])


def downgrade() -> None:
    # Deleting event history requires an explicit retention approval.
    op.drop_index("ix_bc_worker_events_agent_received", table_name="bc_worker_events")
    op.drop_index("ix_bc_worker_events_tenant_received", table_name="bc_worker_events")
    op.drop_table("bc_worker_events")
