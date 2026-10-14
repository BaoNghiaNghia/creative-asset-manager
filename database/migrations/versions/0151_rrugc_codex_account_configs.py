"""Tenant-scoped non-secret Codex account preferences.

Revision ID: 0151_rrugc_codex_account_configs
Revises: 0150_rrugc_stage4_queue_pause
"""
from alembic import op
import sqlalchemy as sa

revision = "0151_rrugc_codex_account_configs"
down_revision = "0150_rrugc_stage4_queue_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_codex_account_configs",
        sa.Column("tenant_id", sa.String(length=255), primary_key=True),
        sa.Column("account_id", sa.String(length=24), primary_key=True),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("stages_json", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(length=255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "max_concurrent BETWEEN 1 AND 3", name="ck_rrugc_codex_account_concurrency",
        ),
    )


def downgrade() -> None:
    op.drop_table("rrugc_codex_account_configs")
