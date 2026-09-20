"""Persist the Phase 4A global video CDN delivery runtime toggle.

Revision ID: 0084_video_cdn_delivery_runtime
Revises: 0083_r2_video_cache_fill

The singleton is seeded disabled. This migration changes no playback behavior
and performs no R2 operation.
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0084_video_cdn_delivery_runtime"
down_revision = "0083_r2_video_cache_fill"
branch_labels = None
depends_on = None

SETTING_KEY = "VIDEO_CDN_DELIVERY_ENABLED"


def upgrade() -> None:
    settings = op.create_table(
        "video_delivery_runtime_settings",
        sa.Column("setting_key", sa.String(length=64), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("update_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "setting_key = 'VIDEO_CDN_DELIVERY_ENABLED'",
            name="ck_video_delivery_runtime_setting_key",
        ),
    )
    now = datetime.now(timezone.utc)
    op.get_bind().execute(
        settings.insert().values(
            setting_key=SETTING_KEY,
            enabled=False,
            created_at=now,
            updated_at=now,
        )
    )


def downgrade() -> None:
    op.drop_table("video_delivery_runtime_settings")
