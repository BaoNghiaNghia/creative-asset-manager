"""Cap Stack Gemini vision metadata, kept distinct from manual review.

Revision ID: 0155_rrugc_cap_stack_gemini
Revises: 0154_rrugc_cap_stack_scout
"""
from alembic import op
import sqlalchemy as sa

revision = "0155_rrugc_cap_stack_gemini"
down_revision = "0154_rrugc_cap_stack_scout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, field in (
        ("analysis_revision", sa.Column("analysis_revision", sa.Integer(), nullable=False, server_default="1")),
        ("is_cap_stack", sa.Column("is_cap_stack", sa.Boolean(), nullable=True)),
        ("ai_suspected", sa.Column("ai_suspected", sa.Boolean(), nullable=True)),
        ("ai_suspicion_confidence", sa.Column("ai_suspicion_confidence", sa.Float(), nullable=True)),
        ("visual_summary", sa.Column("visual_summary", sa.Text(), nullable=True)),
        ("analysis_model", sa.Column("analysis_model", sa.String(128), nullable=True)),
        ("analysis_error_code", sa.Column("analysis_error_code", sa.String(100), nullable=True)),
        ("analyzed_at", sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True)),
    ):
        op.add_column("rrugc_cap_stack_images", field)


def downgrade() -> None:
    for name in (
        "analyzed_at", "analysis_error_code", "analysis_model",
        "visual_summary", "ai_suspicion_confidence", "ai_suspected",
        "is_cap_stack", "analysis_revision",
    ):
        op.drop_column("rrugc_cap_stack_images", name)
