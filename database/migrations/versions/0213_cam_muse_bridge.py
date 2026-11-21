"""Muse job receipts for tenant-scoped Picked Etsy products.

Revision ID: 0213_cam_muse_bridge
Revises: 0212_etsy_spy_listing_image_deletions
"""
from alembic import op
import sqlalchemy as sa

revision = "0213_cam_muse_bridge"
down_revision = "0212_etsy_spy_listing_image_deletions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_muse_jobs",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("listing_key", sa.String(150), primary_key=True),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(700), nullable=False),
        sa.Column("keyword", sa.String(220)),
        sa.Column("artwork_sha256", sa.String(64)),
        sa.Column("artwork_asset_path", sa.String(250)),
        sa.Column("product_image_url", sa.String(1024)),
        sa.Column("source_state", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("lease_hash", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_receipts", sa.JSON()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "listing_key"],
            ["etsy_spy_picked.tenant_id", "etsy_spy_picked.listing_key"],
            name="fk_cam_muse_picked", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_cam_muse_status", "cam_muse_jobs",
                    ["tenant_id", "status", "picked_at"])


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM cam_muse_jobs")):
        raise RuntimeError("Refusing to drop Muse job receipts")
    op.drop_index("ix_cam_muse_status", table_name="cam_muse_jobs")
    op.drop_table("cam_muse_jobs")
