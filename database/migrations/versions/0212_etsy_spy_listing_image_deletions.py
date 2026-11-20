"""Track individual user-deleted listing images and prevent old PNG resync.

Revision ID: 0212_etsy_spy_listing_image_deletions
Revises: 0211_video_cache_30d_retention
"""
from alembic import op
import sqlalchemy as sa

revision = "0212_etsy_spy_listing_image_deletions"
down_revision = "0211_video_cache_30d_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_listing_image_deletions",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("listing_key", sa.String(150), primary_key=True),
        sa.Column("pack_id", sa.String(64), primary_key=True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("output_sha256", sa.String(64), primary_key=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    # V13 image packs have 13 slots; the original 0210 constraint covered
    # only 01-10 and blocked the additional C1717 variant/benefit photos.
    with op.batch_alter_table("etsy_spy_listing_images") as batch:
        batch.drop_constraint("ck_etsy_spy_listing_image_slot", type_="check")
        batch.create_check_constraint("ck_etsy_spy_listing_image_slot",
                                      "slot BETWEEN 1 AND 13")


def downgrade():
    bind = op.get_bind()
    with op.batch_alter_table("etsy_spy_listing_images") as batch:
        if bind.scalar(sa.text(
            "SELECT COUNT(*) FROM etsy_spy_listing_images WHERE slot > 10"
        )):
            raise RuntimeError("Cannot downgrade while V13 listing slots exist")
        batch.drop_constraint("ck_etsy_spy_listing_image_slot", type_="check")
        batch.create_check_constraint("ck_etsy_spy_listing_image_slot",
                                      "slot BETWEEN 1 AND 10")
    if bind.scalar(sa.text(
        "SELECT COUNT(*) FROM etsy_spy_listing_image_deletions"
    )):
        raise RuntimeError("Cannot drop listing-image deletion receipts")
    op.drop_table("etsy_spy_listing_image_deletions")
