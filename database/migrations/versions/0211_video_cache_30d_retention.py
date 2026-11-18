"""Durable aggregate audit for absolute 30-day R2 video-cache retention.

Revision ID: 0211_video_cache_30d_retention
Revises: 0210_etsy_spy_listing_images
"""
from alembic import op
import sqlalchemy as sa

revision = "0211_video_cache_30d_retention"
down_revision = "0210_etsy_spy_listing_images"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_cache_cleanup_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expired_deleted", sa.Integer(), nullable=False),
        sa.Column("expired_bytes_freed", sa.BigInteger(), nullable=False),
        sa.Column("remaining_expired_count", sa.Integer(), nullable=False),
        sa.Column("remaining_expired_bytes", sa.BigInteger(), nullable=False),
        sa.Column("delete_failures", sa.Integer(), nullable=False),
        sa.Column("capacity_evicted", sa.Integer(), nullable=False),
        sa.Column("recovered", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "expired_deleted >= 0 AND expired_bytes_freed >= 0",
            name="ck_video_cache_retention_nonnegative",
        ),
    )
    op.create_index("ix_video_cache_cleanup_runs_finished",
                    "video_cache_cleanup_runs", ["finished_at"])


def downgrade():
    # Historical cleanup run metrics do not affect source media or R2 objects.
    op.drop_index("ix_video_cache_cleanup_runs_finished",
                  table_name="video_cache_cleanup_runs")
    op.drop_table("video_cache_cleanup_runs")
