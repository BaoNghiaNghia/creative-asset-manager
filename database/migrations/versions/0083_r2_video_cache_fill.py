"""Add durable fill ownership and cleanup leases to R2 video cache rows.

Revision ID: 0083_r2_video_cache_fill
Revises: 0082_r2_video_cache_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "0083_r2_video_cache_fill"
down_revision = "0082_r2_video_cache_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("video_cache_objects", sa.Column("fill_job_id", sa.String(36)))
    op.add_column("video_cache_objects", sa.Column("fill_generation", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("video_cache_objects", sa.Column("multipart_upload_id", sa.String(255)))
    op.add_column("video_cache_objects", sa.Column("cleanup_claimed_by", sa.String(255)))
    op.add_column("video_cache_objects", sa.Column("cleanup_lease_expires_at", sa.DateTime(timezone=True)))
    # Keep remote-byte ledger rows after asset/source deletion until R2 delete succeeds.
    # Source ownership is checked on admission and again by the fill worker.
    with op.batch_alter_table("video_cache_objects") as batch:
        batch.drop_constraint("fk_video_cache_tenant_asset", type_="foreignkey")
        batch.drop_constraint("fk_video_cache_tenant_source_asset", type_="foreignkey")
    op.create_index("ix_video_cache_lru", "video_cache_objects", ["status", "last_accessed_at", "cached_at", "id"])
    op.create_index("ix_video_cache_cleanup_due", "video_cache_objects", ["status", "next_attempt_at", "cleanup_lease_expires_at"])


def downgrade():
    # Rollback requires a preflight that no cache row references a deleted asset/source.
    with op.batch_alter_table("video_cache_objects") as batch:
        batch.create_foreign_key(
            "fk_video_cache_tenant_asset", "assets", ["tenant_id", "asset_id"],
            ["tenant_id", "id"], ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_video_cache_tenant_source_asset", "source_assets",
            ["tenant_id", "source_asset_id"], ["tenant_id", "id"], ondelete="CASCADE",
        )
    op.drop_index("ix_video_cache_cleanup_due", table_name="video_cache_objects")
    op.drop_index("ix_video_cache_lru", table_name="video_cache_objects")
    op.drop_column("video_cache_objects", "cleanup_lease_expires_at")
    op.drop_column("video_cache_objects", "cleanup_claimed_by")
    op.drop_column("video_cache_objects", "multipart_upload_id")
    op.drop_column("video_cache_objects", "fill_generation")
    op.drop_column("video_cache_objects", "fill_job_id")
