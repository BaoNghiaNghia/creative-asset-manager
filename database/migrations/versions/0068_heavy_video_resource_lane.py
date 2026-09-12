"""add durable global heavy video resource lane.

Revision ID: 0068_heavy_video_resource_lane
Revises: 0067_video_generation_domain
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0068_heavy_video_resource_lane"
down_revision = "0067_video_generation_domain"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "processing_resource_leases",
        sa.Column("resource_key", sa.String(length=64), primary_key=True),
        sa.Column("owner_type", sa.String(length=64), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.bulk_insert(
        sa.table(
            "processing_resource_leases",
            sa.column("resource_key", sa.String()),
            sa.column("owner_type", sa.String()),
            sa.column("owner_id", sa.String()),
            sa.column("acquired_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [{
            "resource_key": "heavy_video",
            "owner_type": None,
            "owner_id": None,
            "acquired_at": None,
            "updated_at": datetime.now(timezone.utc),
        }],
    )


def downgrade():
    op.drop_table("processing_resource_leases")
