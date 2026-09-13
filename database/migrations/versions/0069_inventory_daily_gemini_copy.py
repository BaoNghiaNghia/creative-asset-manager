"""persist daily Gemini working-copy authority

Revision ID: 0069_inventory_daily_gemini_copy
Revises: 0068_heavy_video_resource_lane
"""

from alembic import op
import sqlalchemy as sa


revision = "0069_inventory_daily_gemini_copy"
down_revision = "0068_heavy_video_resource_lane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inventory_daily_sheet_snapshots",
        sa.Column("gemini_file_id", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inventory_daily_sheet_snapshots", "gemini_file_id")
