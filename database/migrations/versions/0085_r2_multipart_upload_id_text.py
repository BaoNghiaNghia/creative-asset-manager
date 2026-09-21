"""Allow opaque R2 multipart upload IDs longer than 255 characters.

Revision ID: 0085_r2_multipart_upload_id_text
Revises: 0084_video_cdn_delivery_runtime

This is a metadata-only widening on PostgreSQL. The downgrade refuses to
narrow the column while values that would be truncated still exist.
"""

import sqlalchemy as sa
from alembic import op

revision = "0085_r2_multipart_upload_id_text"
down_revision = "0084_video_cdn_delivery_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_cache_objects") as batch_op:
        batch_op.alter_column(
            "multipart_upload_id",
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    too_long = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM video_cache_objects "
            "WHERE length(multipart_upload_id) > 255 LIMIT 1"
        )
    ).scalar()
    if too_long is not None:
        raise RuntimeError(
            "Cannot downgrade multipart_upload_id to VARCHAR(255) while "
            "longer R2 upload IDs exist"
        )

    with op.batch_alter_table("video_cache_objects") as batch_op:
        batch_op.alter_column(
            "multipart_upload_id",
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=True,
        )
