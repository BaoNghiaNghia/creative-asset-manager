"""add durable public review rate limit buckets

Revision ID: 0081_public_review_rate_limits
Revises: 0080_public_review_phase_1
"""
from alembic import op
import sqlalchemy as sa
revision = "0081_public_review_rate_limits"
down_revision = "0080_public_review_phase_1"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("public_review_rate_limits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("client_digest", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation", "client_digest", "window_start", name="uq_public_review_rate_limits_window"),
        sa.CheckConstraint("request_count > 0", name="ck_public_review_rate_limits_count"),
    )
    op.create_index("ix_public_review_rate_limits_window", "public_review_rate_limits", ["window_start"])

def downgrade():
    op.drop_index("ix_public_review_rate_limits_window", table_name="public_review_rate_limits")
    op.drop_table("public_review_rate_limits")
