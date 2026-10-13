"""Tenant-specific Stage 4 generation queue pause.

Revision ID: 0150_rrugc_stage4_queue_pause
Revises: 0149_rrugc_auto_keyword_replay
"""
from alembic import op
import sqlalchemy as sa

revision = "0150_rrugc_stage4_queue_pause"
down_revision = "0149_rrugc_auto_keyword_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_stage4_queue_controls",
        sa.Column("tenant_id", sa.String(length=255), primary_key=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rrugc_stage4_queue_controls")
