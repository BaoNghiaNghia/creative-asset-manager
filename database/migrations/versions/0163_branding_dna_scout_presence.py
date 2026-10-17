"""Isolated paired Branding DNA agent presence; no scan dispatch.

Revision ID: 0163_branding_dna_scout_presence
Revises: 0162_branding_dna_handoff
"""
from alembic import op
import sqlalchemy as sa

revision = "0163_branding_dna_scout_presence"
down_revision = "0162_branding_dna_handoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bc_scout_sessions",
        sa.Column("agent_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("shop_count", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_id"], ["rrugc_scout_agents.tenant_id", "rrugc_scout_agents.id"],
            name="fk_bc_scout_session_paired", ondelete="CASCADE",
        ),
        sa.CheckConstraint("shop_count >= 0 AND shop_count <= 200", name="ck_bc_scout_sessions_shop_count"),
    )
    op.create_index("ix_bc_scout_sessions_tenant_last_seen", "bc_scout_sessions", ["tenant_id", "last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_bc_scout_sessions_tenant_last_seen", table_name="bc_scout_sessions")
    op.drop_table("bc_scout_sessions")
