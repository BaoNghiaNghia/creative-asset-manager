"""Durable Scout feedback, priority queue, and per-cycle metrics.

Revision ID: 0139_rrugc_scout_feedback
Revises: 0138_dynamic_gemini_key_pools
"""
from alembic import op
import sqlalchemy as sa

revision = "0139_rrugc_scout_feedback"
down_revision = "0138_dynamic_gemini_key_pools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_scout_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_key", sa.String(500), nullable=False),
        sa.Column("display_value", sa.String(2048), nullable=False),
        sa.Column("keyword", sa.String(500), nullable=False),
        sa.Column("source_image_url", sa.String(2048)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("updated_by_user_id", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by_agent_id", sa.String(36)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "target_type", "target_key", name="uq_rrugc_scout_feedback_target"),
    )
    op.create_index("ix_rrugc_scout_feedback_queue", "rrugc_scout_feedback", ["tenant_id", "status", "processed_at", "lease_expires_at"])
    op.create_table(
        "rrugc_scout_metric_cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("machine_label", sa.String(160), nullable=False),
        sa.Column("cycle_id", sa.String(64), nullable=False),
        sa.Column("scanned_pins", sa.Integer(), nullable=False),
        sa.Column("found_quotes", sa.Integer(), nullable=False),
        sa.Column("new_keywords", sa.Integer(), nullable=False),
        sa.Column("duplicate_pins", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "agent_id", "mode", "cycle_id", name="uq_rrugc_scout_metric_cycle"),
    )
    op.create_index("ix_rrugc_scout_metric_cycle_tenant", "rrugc_scout_metric_cycles", ["tenant_id", "mode", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_rrugc_scout_metric_cycle_tenant", table_name="rrugc_scout_metric_cycles")
    op.drop_table("rrugc_scout_metric_cycles")
    op.drop_index("ix_rrugc_scout_feedback_queue", table_name="rrugc_scout_feedback")
    op.drop_table("rrugc_scout_feedback")
