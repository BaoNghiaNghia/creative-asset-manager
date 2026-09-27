"""Add Realistic Review UGC reference analysis fields.

Revision ID: 0094_rrugc_reference_analysis
Revises: 0093_realistic_review_ugc
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0094_rrugc_reference_analysis"
down_revision = "0093_realistic_review_ugc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    campaign_columns = (
        ("min_head_ratio", sa.Float(), "0.20"),
        ("max_head_ratio", sa.Float(), "0.45"),
        ("min_smile_score", sa.Float(), "0.65"),
        ("max_head_occlusion", sa.Float(), "0.25"),
        ("max_ai_risk_score", sa.Float(), "0.20"),
        ("min_quality_score", sa.Float(), "0.55"),
        ("min_ugc_score", sa.Float(), "0.55"),
        ("min_product_fit_score", sa.Float(), "0.55"),
    )
    for name, type_, default in campaign_columns:
        op.add_column(
            "rrugc_campaigns",
            sa.Column(name, type_, nullable=False, server_default=default),
        )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("require_head_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("reject_headwear", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.add_column(
        "rrugc_candidates",
        sa.Column("analysis_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "rrugc_candidates",
        sa.Column("import_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("rrugc_candidates", sa.Column("people_count", sa.Integer()))
    op.add_column("rrugc_candidates", sa.Column("primary_head_ratio", sa.Float()))
    op.add_column("rrugc_candidates", sa.Column("smile_score", sa.Float()))
    op.add_column("rrugc_candidates", sa.Column("head_visible", sa.Boolean()))
    op.add_column("rrugc_candidates", sa.Column("existing_headwear", sa.Boolean()))
    op.add_column("rrugc_candidates", sa.Column("head_occlusion", sa.Float()))
    op.add_column("rrugc_candidates", sa.Column("mobile_ugc_score", sa.Float()))
    op.add_column("rrugc_candidates", sa.Column("quality_score", sa.Float()))
    op.add_column("rrugc_candidates", sa.Column("ai_risk_score", sa.Float()))
    op.add_column("rrugc_candidates", sa.Column("product_fit_score", sa.Float()))
    op.add_column("rrugc_candidates", sa.Column("final_score", sa.Float()))
    op.add_column("rrugc_candidates", sa.Column("reject_reason", sa.String(length=64)))
    op.add_column("rrugc_candidates", sa.Column("analyzer_provider", sa.String(length=64)))
    op.add_column("rrugc_candidates", sa.Column("analyzer_model", sa.String(length=128)))
    op.add_column("rrugc_candidates", sa.Column("analyzer_version", sa.String(length=64)))
    op.add_column("rrugc_candidates", sa.Column("analysis_summary", sa.Text()))
    op.add_column("rrugc_candidates", sa.Column("analyzed_at", sa.DateTime(timezone=True)))

    # Legacy candidates were discovered before an analyzer queue existed. Keep
    # them visible and let the next explicit re-analysis or Scout replay queue
    # work rather than silently pretending they were analyzed.
    op.execute(
        "UPDATE rrugc_candidates SET analysis_revision = 1 "
        "WHERE analysis_revision IS NULL"
    )


def downgrade() -> None:
    candidate_columns = (
        "analyzed_at",
        "analysis_summary",
        "analyzer_version",
        "analyzer_model",
        "analyzer_provider",
        "reject_reason",
        "final_score",
        "product_fit_score",
        "ai_risk_score",
        "quality_score",
        "mobile_ugc_score",
        "head_occlusion",
        "existing_headwear",
        "head_visible",
        "smile_score",
        "primary_head_ratio",
        "people_count",
        "import_revision",
        "analysis_revision",
    )
    for name in candidate_columns:
        op.drop_column("rrugc_candidates", name)

    campaign_columns = (
        "reject_headwear",
        "require_head_visible",
        "min_product_fit_score",
        "min_ugc_score",
        "min_quality_score",
        "max_ai_risk_score",
        "max_head_occlusion",
        "min_smile_score",
        "max_head_ratio",
        "min_head_ratio",
    )
    for name in campaign_columns:
        op.drop_column("rrugc_campaigns", name)
