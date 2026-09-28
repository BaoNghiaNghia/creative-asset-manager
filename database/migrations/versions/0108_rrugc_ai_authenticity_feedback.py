"""Add RRUGC AI authenticity evidence and manual feedback.

Revision ID: 0108_rrugc_ai_authenticity_feedback
Revises: 0107_rrugc_lifestyle_search_preset
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0108_rrugc_ai_authenticity_feedback"
down_revision = "0107_rrugc_lifestyle_search_preset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rrugc_candidates", sa.Column("ai_risk_raw_score", sa.Float(), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("ai_detector_confidence", sa.Float(), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("ai_risk_confirmed", sa.Boolean(), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("ai_signal_json", sa.JSON(), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("ai_manual_label", sa.String(length=16), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("ai_manual_note", sa.Text(), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("ai_manual_reviewed_by_user_id", sa.String(length=255), nullable=True))
    op.add_column("rrugc_candidates", sa.Column("ai_manual_reviewed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "rrugc_ai_feedback",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("ai_risk_raw_score", sa.Float(), nullable=True),
        sa.Column("ai_risk_score", sa.Float(), nullable=True),
        sa.Column("detector_confidence", sa.Float(), nullable=True),
        sa.Column("analyzer_version", sa.String(length=64), nullable=True),
        sa.Column("signal_json", sa.JSON(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["rrugc_candidates.tenant_id", "rrugc_candidates.id"],
            name="fk_rrugc_ai_feedback_candidate",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_rrugc_ai_feedback_tenant_label_created",
        "rrugc_ai_feedback",
        ["tenant_id", "label", "created_at"],
    )
    op.create_index(
        "ix_rrugc_ai_feedback_candidate_created",
        "rrugc_ai_feedback",
        ["tenant_id", "candidate_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rrugc_ai_feedback_candidate_created", table_name="rrugc_ai_feedback")
    op.drop_index("ix_rrugc_ai_feedback_tenant_label_created", table_name="rrugc_ai_feedback")
    op.drop_table("rrugc_ai_feedback")
    op.drop_column("rrugc_candidates", "ai_manual_reviewed_at")
    op.drop_column("rrugc_candidates", "ai_manual_reviewed_by_user_id")
    op.drop_column("rrugc_candidates", "ai_manual_note")
    op.drop_column("rrugc_candidates", "ai_manual_label")
    op.drop_column("rrugc_candidates", "ai_signal_json")
    op.drop_column("rrugc_candidates", "ai_risk_confirmed")
    op.drop_column("rrugc_candidates", "ai_detector_confidence")
    op.drop_column("rrugc_candidates", "ai_risk_raw_score")
