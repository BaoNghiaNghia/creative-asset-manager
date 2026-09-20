"""Add private R2 original-video cache metadata.

Revision ID: 0082_r2_video_cache_foundation
Revises: 0081_public_review_rate_limits
"""
from alembic import op
import sqlalchemy as sa

revision = "0082_r2_video_cache_foundation"
down_revision = "0081_public_review_rate_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_cache_objects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("source_asset_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("r2_key", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("etag", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False, server_default="preparing"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cached_at", sa.DateTime(timezone=True)),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_video_cache_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"], ondelete="CASCADE", name="fk_video_cache_tenant_asset"),
        sa.ForeignKeyConstraint(["tenant_id", "source_asset_id"], ["source_assets.tenant_id", "source_assets.id"], ondelete="CASCADE", name="fk_video_cache_tenant_source_asset"),
        sa.UniqueConstraint("tenant_id", "content_hash", name="uq_video_cache_tenant_hash"),
        sa.UniqueConstraint("r2_key", name="uq_video_cache_r2_key"),
        sa.CheckConstraint("status IN ('preparing','ready','deleting','retry','failed')", name="ck_video_cache_status"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_video_cache_size"),
        sa.CheckConstraint("reserved_bytes >= 0", name="ck_video_cache_reserved"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_video_cache_attempts"),
        sa.CheckConstraint("mime_type LIKE 'video/%'", name="ck_video_cache_video_mime"),
    )
    op.create_index("ix_video_cache_status", "video_cache_objects", ["status"])
    op.create_index("ix_video_cache_status_next_attempt", "video_cache_objects", ["status", "next_attempt_at"])
    op.create_index("ix_video_cache_last_accessed", "video_cache_objects", ["last_accessed_at"])
    op.create_index("ix_video_cache_tenant_status", "video_cache_objects", ["tenant_id", "status"])


def downgrade():
    op.drop_index("ix_video_cache_tenant_status", table_name="video_cache_objects")
    op.drop_index("ix_video_cache_last_accessed", table_name="video_cache_objects")
    op.drop_index("ix_video_cache_status_next_attempt", table_name="video_cache_objects")
    op.drop_index("ix_video_cache_status", table_name="video_cache_objects")
    op.drop_table("video_cache_objects")
