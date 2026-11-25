"""Store the per-picked listing idea draft in the existing CAM database.

Revision ID: 0216_etsy_listing_idea_draft
Revises: 0215_cam_muse_lightweight_tasks
"""
from alembic import op
import sqlalchemy as sa

revision = "0216_etsy_listing_idea_draft"
down_revision = "0215_cam_muse_lightweight_tasks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("etsy_spy_picked", sa.Column("listing_idea_draft", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("etsy_spy_picked", "listing_idea_draft")
