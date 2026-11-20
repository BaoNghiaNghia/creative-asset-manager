"""Track explicit Listing Image deletions and their Google Drive cleanup receipts.

Revision ID: 0212_etsy_spy_listing_image_delete_drive
Revises: 0211_video_cache_30d_retention
"""
from alembic import op
import sqlalchemy as sa

revision = "0212_etsy_spy_listing_image_delete_drive"
down_revision = "0211_video_cache_30d_retention"
branch_labels = None
depends_on = None


def upgrade():
    # Existing 0210 schema only allowed slots 01-10. V13 uses 01-13.
    with op.batch_alter_table("etsy_spy_listing_images") as batch:
        batch.drop_constraint("ck_etsy_spy_listing_image_slot", type_="check")
        batch.create_check_constraint("ck_etsy_spy_listing_image_slot",
                                      "slot BETWEEN 1 AND 13")
    op.create_table(
        "etsy_spy_listing_image_deletions",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("listing_key", sa.String(150), primary_key=True),
        sa.Column("pack_id", sa.String(64), primary_key=True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("output_sha256", sa.String(64), primary_key=True),
        sa.Column("garment_code", sa.String(10), nullable=False),
        sa.Column("design_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("placement", sa.String(48), nullable=False),
        sa.Column("thumbnail_sha256", sa.String(64), nullable=False),
        sa.Column("drive_file_id", sa.String(128), nullable=False),
        sa.Column("drive_thumbnail_id", sa.String(128), nullable=False),
        sa.Column("drive_folder_id", sa.String(128), nullable=False),
        sa.Column("drive_thumbnail_folder_id", sa.String(128), nullable=False),
        sa.Column("drive_manifest_id", sa.String(128), nullable=True),
        sa.Column("original_deleted", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("thumbnail_deleted", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("manifest_deleted", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("state", sa.String(16), nullable=False,
                  server_default="pending"),
        sa.Column("regeneration_status", sa.String(16), nullable=False,
                  server_default="waiting"),
        sa.Column("requested_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("slot BETWEEN 1 AND 13",
                           name="ck_etsy_spy_image_delete_slot"),
        sa.CheckConstraint("state IN ('pending','deleted')",
                           name="ck_etsy_spy_image_delete_state"),
        sa.CheckConstraint("regeneration_status IN ('waiting','pending','finished')",
                           name="ck_etsy_spy_image_delete_regeneration_status"),
    )
    op.create_index(
        "ix_etsy_spy_listing_image_deletions_pending",
        "etsy_spy_listing_image_deletions", ["state", "requested_at"])


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT COUNT(*) FROM etsy_spy_listing_image_deletions"
    )):
        raise RuntimeError(
            "Cannot discard deleted ImageGen SHA receipts or recreate-pending slots")
    if bind.scalar(sa.text(
        "SELECT COUNT(*) FROM etsy_spy_listing_images WHERE slot > 10"
    )):
        raise RuntimeError("Cannot reapply 10-role limit while V13 PNGs exist")
    op.drop_index("ix_etsy_spy_listing_image_deletions_pending",
                  table_name="etsy_spy_listing_image_deletions")
    op.drop_table("etsy_spy_listing_image_deletions")
    with op.batch_alter_table("etsy_spy_listing_images") as batch:
        batch.drop_constraint("ck_etsy_spy_listing_image_slot", type_="check")
        batch.create_check_constraint("ck_etsy_spy_listing_image_slot",
                                      "slot BETWEEN 1 AND 10")
