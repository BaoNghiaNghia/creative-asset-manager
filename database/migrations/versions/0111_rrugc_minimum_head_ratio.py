"""Raise RRUGC minimum head ratio for usable cap references.

Revision ID: 0111_rrugc_minimum_head_ratio
Revises: 0110_rrugc_smartphone_style_scores
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0111_rrugc_minimum_head_ratio"
down_revision = "0110_rrugc_smartphone_style_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Preserve explicit campaign tuning; only move campaigns that still use
    # the previous built-in 10% minimum.
    bind.execute(sa.text(
        """
        UPDATE rrugc_campaigns
        SET min_head_ratio = 0.18
        WHERE ABS(min_head_ratio - 0.10) < 0.000001
        """
    ))

    # Reclassify already-approved references whose primary head is now too
    # small to provide enough detail for reliable cap/headwear replacement.
    bind.execute(sa.text(
        """
        UPDATE rrugc_candidates AS candidate
        SET status = 'rejected_head_ratio',
            reject_reason = 'HEAD_RATIO_OUT_OF_RANGE',
            updated_at = CURRENT_TIMESTAMP
        FROM rrugc_campaigns AS campaign
        WHERE candidate.tenant_id = campaign.tenant_id
          AND candidate.campaign_id = campaign.id
          AND candidate.status = 'approved'
          AND candidate.primary_head_ratio IS NOT NULL
          AND candidate.primary_head_ratio < campaign.min_head_ratio
        """
    ))


def downgrade() -> None:
    # Do not silently restore old approvals or lower user-visible campaign
    # thresholds during rollback.
    pass
