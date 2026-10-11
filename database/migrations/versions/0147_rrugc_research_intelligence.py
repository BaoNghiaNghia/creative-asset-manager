"""Add tenant-scoped Research Intelligence inbox for RRUGC Stage 0.

Revision ID: 0147_rrugc_research_intelligence
Revises: 0146_rrugc_keyword_single_attempt

Additive schema: no changes to previous keyword, Scout or UGC tables.
"""
from alembic import op
import sqlalchemy as sa

revision = "0147_rrugc_research_intelligence"
down_revision = "0146_rrugc_keyword_single_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_research_signals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(80), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("excerpt", sa.String(480), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("keyword", sa.String(150), nullable=True),
        sa.Column("created_by_user_id", sa.String(255), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "fingerprint", name="uq_rrugc_research_tenant_fingerprint"),
    )
    op.create_index(
        "ix_rrugc_research_tenant_status",
        "rrugc_research_signals", ["tenant_id", "status", "created_at"],
    )


def downgrade() -> None:
    # Explicit rollback: a destructive downgrade removes submitted research
    # evidence. Export the table first and do not auto-run in production.
    op.drop_index("ix_rrugc_research_tenant_status", table_name="rrugc_research_signals")
    op.drop_table("rrugc_research_signals")
