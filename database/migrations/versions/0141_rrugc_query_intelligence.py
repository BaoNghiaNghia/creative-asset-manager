"""Durable cross-machine query pool and performance scores.

Revision ID: 0141_rrugc_query_intelligence
Revises: 0140_rrugc_keyword_trademark
"""
from alembic import op
import sqlalchemy as sa

revision = "0141_rrugc_query_intelligence"
down_revision = "0140_rrugc_keyword_trademark"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_scout_queries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("query", sa.String(240), nullable=False),
        sa.Column("query_normalized", sa.String(240), nullable=False),
        sa.Column("lane", sa.String(20), nullable=False),
        sa.Column("source_keyword", sa.String(500)),
        sa.Column("completed_cycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_cycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scanned_pins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("found_quotes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_keywords", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_pins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("empty_cycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by_agent_id", sa.String(36)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_searched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "query_normalized", name="uq_rrugc_scout_query_unique"),
    )
    op.create_index("ix_rrugc_scout_query_lease", "rrugc_scout_queries", ["tenant_id", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_rrugc_scout_query_lease", table_name="rrugc_scout_queries")
    op.drop_table("rrugc_scout_queries")
