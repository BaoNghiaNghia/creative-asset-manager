"""freeze exact prompt content per logical Inventory operation.

Revision ID: 0072_inventory_prompt_snapshot_freeze
Revises: 0071_inventory_prompt_versions
"""
from alembic import op
import sqlalchemy as sa

revision = "0072_prompt_snapshot_freeze"
down_revision = "0071_inventory_prompt_versions"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("inventory_daily_sheet_snapshots", sa.Column("gemini_prompt_content", sa.Text()))
    op.add_column("inventory_daily_carry_forwards", sa.Column("prompt_content", sa.Text()))

def downgrade():
    op.drop_column("inventory_daily_carry_forwards", "prompt_content")
    op.drop_column("inventory_daily_sheet_snapshots", "gemini_prompt_content")
