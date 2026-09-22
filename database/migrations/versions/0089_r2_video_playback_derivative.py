"""Add derived Public Review playback metadata.

Revision ID: 0089_r2_video_playback_derivative
Revises: 0088_public_review_encrypted_secret
"""
from alembic import op
import sqlalchemy as sa

revision = "0089_r2_video_playback_derivative"
down_revision = "0088_public_review_encrypted_secret"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("video_cache_objects", sa.Column("playback_r2_key", sa.String(512), nullable=True))
    op.add_column("video_cache_objects", sa.Column("playback_kind", sa.String(32), nullable=True))
    op.add_column(
        "video_cache_objects",
        sa.Column("playback_status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.add_column(
        "video_cache_objects",
        sa.Column("playback_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "video_cache_objects",
        sa.Column("playback_reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("video_cache_objects", sa.Column("playback_etag", sa.String(255), nullable=True))
    op.add_column("video_cache_objects", sa.Column("playback_job_id", sa.String(36), nullable=True))
    op.add_column(
        "video_cache_objects",
        sa.Column("playback_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("video_cache_objects", sa.Column("playback_error_code", sa.String(100), nullable=True))
    op.add_column(
        "video_cache_objects",
        sa.Column("playback_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Batch mode keeps SQLite migration tests and PostgreSQL production on the
    # same named constraints without relying on SQLite ALTER CONSTRAINT support.
    with op.batch_alter_table("video_cache_objects") as batch:
        batch.create_check_constraint(
            "ck_video_cache_playback_status",
            "playback_status IN ('pending','preparing','ready','failed','skipped')",
        )
        batch.create_check_constraint(
            "ck_video_cache_playback_size",
            "playback_size_bytes >= 0",
        )
        batch.create_check_constraint(
            "ck_video_cache_playback_reserved",
            "playback_reserved_bytes >= 0",
        )
        batch.create_check_constraint(
            "ck_video_cache_playback_generation",
            "playback_generation >= 0",
        )
    op.create_index(
        "ix_video_cache_playback_status",
        "video_cache_objects",
        ["playback_status"],
    )


def downgrade():
    op.drop_index("ix_video_cache_playback_status", table_name="video_cache_objects")
    with op.batch_alter_table("video_cache_objects") as batch:
        batch.drop_constraint("ck_video_cache_playback_generation", type_="check")
        batch.drop_constraint("ck_video_cache_playback_reserved", type_="check")
        batch.drop_constraint("ck_video_cache_playback_size", type_="check")
        batch.drop_constraint("ck_video_cache_playback_status", type_="check")
    op.drop_column("video_cache_objects", "playback_updated_at")
    op.drop_column("video_cache_objects", "playback_error_code")
    op.drop_column("video_cache_objects", "playback_generation")
    op.drop_column("video_cache_objects", "playback_job_id")
    op.drop_column("video_cache_objects", "playback_etag")
    op.drop_column("video_cache_objects", "playback_reserved_bytes")
    op.drop_column("video_cache_objects", "playback_size_bytes")
    op.drop_column("video_cache_objects", "playback_status")
    op.drop_column("video_cache_objects", "playback_kind")
    op.drop_column("video_cache_objects", "playback_r2_key")
