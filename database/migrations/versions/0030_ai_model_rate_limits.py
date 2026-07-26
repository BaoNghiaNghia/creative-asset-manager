"""Add shared tenant/provider/model AI start-rate state.

Revision ID: 0030_ai_model_rate_limits
Revises: 0029_processing_job_duration
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_ai_model_rate_limits"
down_revision = "0029_processing_job_duration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_model_rate_limit_state",
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "provider", "model", name="pk_ai_model_rate_limit_state"),
    )
    op.create_index(
        "ix_ai_model_rate_limit_next",
        "ai_model_rate_limit_state",
        ["tenant_id", "provider", "model", "next_eligible_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_model_rate_limit_next", table_name="ai_model_rate_limit_state")
    op.drop_table("ai_model_rate_limit_state")
