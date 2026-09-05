"""add desktop OAuth handoff storage

Revision ID: 0058_desktop_oauth_handoff
Revises: 0057_multi_provider_sources
"""

from alembic import op
import sqlalchemy as sa

revision = "0058_desktop_oauth_handoff"
down_revision = "0057_multi_provider_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "desktop_oauth_handoffs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("desktop_instance_binding_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_binding_hash", sa.String(length=64), nullable=True),
        sa.Column("launch_token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("ticket_hash", sa.String(length=64), nullable=True, unique=True),
        sa.Column("pending_payload_ciphertext", sa.Text(), nullable=True),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("launch_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("callback_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('google', 'microsoft')", name="ck_desktop_oauth_handoffs_provider"),
    )
    op.create_index(
        "ix_desktop_oauth_handoffs_expiry",
        "desktop_oauth_handoffs",
        ["expires_at", "consumed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_desktop_oauth_handoffs_expiry", table_name="desktop_oauth_handoffs")
    op.drop_table("desktop_oauth_handoffs")
