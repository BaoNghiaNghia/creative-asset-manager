"""Add durable Gemini project/model daily quota reservations.

Revision ID: 0031_gemini_project_quota_state
Revises: 0030_ai_model_rate_limits
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_gemini_project_quota_state"
down_revision = "0030_ai_model_rate_limits"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "gemini_project_quota_state",
        sa.Column("quota_scope", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("quota_day", sa.Date(), nullable=False),
        sa.Column("reserved_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reserved_requests >= 0", name="ck_gemini_project_quota_requests"),
        sa.PrimaryKeyConstraint("quota_scope", "model", name="pk_gemini_project_quota_state"),
    )
    op.create_index(
        "ix_gemini_project_quota_reset",
        "gemini_project_quota_state",
        ["quota_scope", "quota_day", "blocked_until"],
    )

def downgrade() -> None:
    op.drop_index("ix_gemini_project_quota_reset", table_name="gemini_project_quota_state")
    op.drop_table("gemini_project_quota_state")
