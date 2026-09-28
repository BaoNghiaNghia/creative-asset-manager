"""Tune RRUGC qualification for candid cap-friendly references.

Revision ID: 0109_rrugc_cap_friendly_references
Revises: 0108_rrugc_ai_authenticity_feedback
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0109_rrugc_cap_friendly_references"
down_revision = "0108_rrugc_ai_authenticity_feedback"
branch_labels = None
depends_on = None


OLD_PRESET = [
    "authentic candid lifestyle portrait",
    "casual family candid lifestyle photo",
    "parent child outdoor candid photo",
    "dad child candid lifestyle photo",
    "casual man selfie natural light",
    "casual woman selfie natural light",
    "everyday casual portrait at home",
    "outdoor candid portrait natural light",
    "embroidered baseball cap casual selfie",
    "corduroy cap casual lifestyle portrait",
]

NEW_PRESET = [
    "baseball cap selfie candid natural light",
    "casual woman baseball cap selfie",
    "casual man baseball cap lifestyle photo",
    "outdoor candid person wearing cap",
    "coffee shop baseball cap candid portrait",
    "car selfie baseball cap natural light",
    "corduroy cap casual lifestyle portrait",
    "authentic candid lifestyle portrait natural light",
    "casual family candid lifestyle photo",
    "everyday casual portrait at home",
]


def upgrade() -> None:
    bind = op.get_bind()

    # Relax only fields that are still at the previous defaults so explicit
    # campaign tuning remains untouched.
    bind.execute(sa.text(
        """
        UPDATE rrugc_campaigns
        SET min_head_ratio = CASE
                WHEN ABS(min_head_ratio - 0.20) < 0.000001 THEN 0.10
                ELSE min_head_ratio
            END,
            max_head_ratio = CASE
                WHEN ABS(max_head_ratio - 0.45) < 0.000001 THEN 0.70
                ELSE max_head_ratio
            END,
            min_smile_score = CASE
                WHEN ABS(min_smile_score - 0.65) < 0.000001 THEN 0.00
                ELSE min_smile_score
            END,
            max_head_occlusion = CASE
                WHEN ABS(max_head_occlusion - 0.25) < 0.000001 THEN 0.65
                ELSE max_head_occlusion
            END,
            min_ugc_score = CASE
                WHEN ABS(min_ugc_score - 0.65) < 0.000001 THEN 0.55
                ELSE min_ugc_score
            END
        """
    ))

    # Replace the earlier built-in preset without touching unrelated custom
    # campaigns.
    bind.execute(
        sa.text(
            """
            UPDATE rrugc_campaigns
            SET query = :query,
                search_queries_json = CAST(:queries AS JSON)
            WHERE query = :legacy_query
            """
        ),
        {
            "query": NEW_PRESET[0],
            "queries": json.dumps(NEW_PRESET),
            "legacy_query": OLD_PRESET[0],
        },
    )

    # Promote already-analyzed real-photo candidates that only failed the
    # previous overly narrow pose/expression gates. AI/quality/no-person
    # rejections are intentionally untouched.
    bind.execute(sa.text(
        """
        UPDATE rrugc_candidates AS candidate
        SET status = 'approved',
            reject_reason = NULL,
            updated_at = CURRENT_TIMESTAMP
        FROM rrugc_campaigns AS campaign
        WHERE candidate.tenant_id = campaign.tenant_id
          AND candidate.campaign_id = campaign.id
          AND candidate.status IN (
              'rejected_head_ratio',
              'rejected_expression',
              'rejected_head_occlusion',
              'rejected_context'
          )
          AND candidate.people_count >= 1
          AND candidate.head_visible IS TRUE
          AND candidate.primary_head_ratio BETWEEN campaign.min_head_ratio AND campaign.max_head_ratio
          AND (
              candidate.head_occlusion <= campaign.max_head_occlusion
              OR (
                  candidate.existing_headwear IS TRUE
                  AND candidate.product_fit_score >= 0.60
                  AND candidate.head_occlusion <= 0.85
              )
          )
          AND candidate.quality_score >= campaign.min_quality_score
          AND candidate.mobile_ugc_score >= campaign.min_ugc_score
          AND candidate.product_fit_score >= campaign.min_product_fit_score
          AND (
              candidate.ai_manual_label = 'real'
              OR (
                  COALESCE(candidate.ai_manual_label, '') <> 'ai'
                  AND candidate.ai_risk_score <= campaign.max_ai_risk_score
              )
          )
        """
    ))


def downgrade() -> None:
    # User-tunable campaign thresholds and promoted candidate decisions should
    # not be silently tightened or rejected again on code rollback.
    pass
