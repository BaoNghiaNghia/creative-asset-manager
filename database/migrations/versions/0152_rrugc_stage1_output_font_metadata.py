"""Persist font source report per immutable Stage 1 generated output.

Revision ID: 0152_rrugc_stage1_output_font_metadata
Revises: 0151_rrugc_codex_account_configs
"""
from alembic import op
import sqlalchemy as sa

revision = "0152_rrugc_stage1_output_font_metadata"
down_revision = "0151_rrugc_codex_account_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rrugc_image_output_versions", sa.Column("font_info_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("rrugc_image_output_versions", "font_info_json")
