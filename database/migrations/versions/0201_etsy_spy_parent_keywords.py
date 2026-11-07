"""Store priority for user-managed Etsy Spy parent/root keywords.

Revision ID: 0201_etsy_spy_parent_keywords
Revises: 0200_etsy_spy_win_keywords
"""
from alembic import op
import sqlalchemy as sa

revision = "0201_etsy_spy_parent_keywords"
down_revision = "0200_etsy_spy_win_keywords"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_parent_keywords",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("keyword_key", sa.String(180), nullable=False),
        sa.Column("keyword", sa.String(180), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "keyword_key"),
    )


def downgrade():
    op.drop_table("etsy_spy_parent_keywords")
