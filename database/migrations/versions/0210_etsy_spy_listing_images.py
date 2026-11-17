"""ImageGen saved listing images per Picked listing, garment and pack slot.

Revision ID: 0210_etsy_spy_listing_images
Revises: 0209_etsy_spy_colormatch_feedback
"""
from alembic import op
import sqlalchemy as sa

revision = "0210_etsy_spy_listing_images"
down_revision = "0209_etsy_spy_colormatch_feedback"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_listing_images",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("listing_key", sa.String(150), primary_key=True),
        sa.Column("pack_id", sa.String(64), primary_key=True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("garment_code", sa.String(10), nullable=False),
        sa.Column("design_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("placement", sa.String(48), nullable=False),
        sa.Column("drive_folder_id", sa.String(128), nullable=False),
        sa.Column("drive_thumbnail_folder_id", sa.String(128), nullable=False),
        sa.Column("drive_manifest_id", sa.String(128), nullable=True),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column("thumbnail_sha256", sa.String(64), nullable=False),
        sa.Column("drive_file_id", sa.String(128), nullable=False),
        sa.Column("drive_thumbnail_id", sa.String(128), nullable=False),
        sa.Column("native_width", sa.Integer(), nullable=True),
        sa.Column("native_height", sa.Integer(), nullable=True),
        sa.Column("source_job_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("slot BETWEEN 1 AND 10",
                           name="ck_etsy_spy_listing_image_slot"),
        sa.CheckConstraint("garment_code IN ('C1717','G500','G180','G185')",
                           name="ck_etsy_spy_listing_image_garment"),
    )
    op.create_index(
        "ix_etsy_spy_listing_image_counts",
        "etsy_spy_listing_images", ["tenant_id", "listing_key", "garment_code"])


def downgrade():
    bind = op.get_bind()
    # These rows reference immutable image assets in private Drive.
    if bind.scalar(sa.text("SELECT COUNT(*) FROM etsy_spy_listing_images")):
        raise RuntimeError("Cannot drop saved ImageGen listing output history")
    op.drop_index("ix_etsy_spy_listing_image_counts",
                  table_name="etsy_spy_listing_images")
    op.drop_table("etsy_spy_listing_images")
