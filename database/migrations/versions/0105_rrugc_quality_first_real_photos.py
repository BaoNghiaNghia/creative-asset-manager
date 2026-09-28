"""Tighten RRUGC reference quality gates for real-photo scouting.

Revision ID: 0105_rrugc_quality_first_real_photos
Revises: 0104_rrugc_campaign_search_queries
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0105_rrugc_quality_first_real_photos"
down_revision = "0104_rrugc_campaign_search_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        """
        UPDATE rrugc_campaigns
        SET max_ai_risk_score = CASE
                WHEN max_ai_risk_score > 0.15 THEN 0.15
                ELSE max_ai_risk_score
            END,
            min_quality_score = CASE
                WHEN min_quality_score < 0.60 THEN 0.60
                ELSE min_quality_score
            END,
            min_ugc_score = CASE
                WHEN min_ugc_score < 0.65 THEN 0.65
                ELSE min_ugc_score
            END
        """
    ))


def downgrade() -> None:
    # Quality thresholds are user-tunable campaign data; do not silently loosen
    # them when rolling code back.
    pass
