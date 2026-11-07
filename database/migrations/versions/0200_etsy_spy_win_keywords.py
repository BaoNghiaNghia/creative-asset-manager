"""Passive tenant-scoped Win Keyword notes for Etsy Spy.

Revision ID: 0200_etsy_spy_win_keywords
Revises: 0199_etsy_spy_logs_12h
"""
from alembic import op
import sqlalchemy as sa

revision = "0200_etsy_spy_win_keywords"
down_revision = "0199_etsy_spy_logs_12h"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_win_keywords",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("keyword_key", sa.String(220), nullable=False),
        sa.Column("keyword", sa.String(220), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "keyword_key"),
    )


def downgrade():
    op.drop_table("etsy_spy_win_keywords")
