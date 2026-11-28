"""Persist optional SHA-bound per-image color metadata; no new table."""
from alembic import op
import sqlalchemy as sa
revision = "0217_listing_image_sms_color"
down_revision = "0216_etsy_listing_idea_draft"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("etsy_spy_listing_images",sa.Column("sms_color_metadata",sa.JSON(),nullable=True))

def downgrade():
    op.drop_column("etsy_spy_listing_images","sms_color_metadata")
