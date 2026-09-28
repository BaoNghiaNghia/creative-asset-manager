"""Add RRUGC smartphone-style preference scores.

Revision ID: 0110_rrugc_smartphone_style_scores
Revises: 0109_rrugc_cap_friendly_references
"""
from __future__ import annotations

from alembic import op
import json
import sqlalchemy as sa


revision = "0110_rrugc_smartphone_style_scores"
down_revision = "0109_rrugc_cap_friendly_references"
branch_labels = None
depends_on = None


OLD_PRESET = [
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

SMARTPHONE_PRESET = [
    "baseball cap selfie iphone natural light",
    "baseball cap mirror selfie casual outfit",
    "woman wearing cap iphone selfie candid",
    "man wearing cap casual phone photo",
    "car selfie baseball cap natural light",
    "coffee shop selfie baseball cap phone photo",
    "corduroy cap selfie candid smartphone",
    "casual selfie iphone natural light",
    "candid phone photo at home",
    "casual family candid smartphone photo",
]


def upgrade() -> None:
    op.add_column(
        "rrugc_candidates",
        sa.Column("phone_authenticity_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "rrugc_candidates",
        sa.Column("artistic_editorial_risk", sa.Float(), nullable=True),
    )

    # Historical candidates predate the dedicated scores. Seed a conservative
    # proxy from the existing UGC score for ranking/display only. Fresh or
    # retried analysis will overwrite these with the v6 analyzer scores.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE rrugc_campaigns
            SET query = :query,
                search_queries_json = CAST(:queries AS JSON)
            WHERE query = :old_query
            """
        ),
        {
            "query": SMARTPHONE_PRESET[0],
            "queries": json.dumps(SMARTPHONE_PRESET),
            "old_query": OLD_PRESET[0],
        },
    )

    op.execute(
        """
        UPDATE rrugc_candidates
        SET phone_authenticity_score = mobile_ugc_score,
            artistic_editorial_risk = CASE
                WHEN mobile_ugc_score IS NULL THEN NULL
                ELSE GREATEST(0.0, LEAST(1.0, 1.0 - mobile_ugc_score))
            END
        WHERE phone_authenticity_score IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("rrugc_candidates", "artistic_editorial_risk")
    op.drop_column("rrugc_candidates", "phone_authenticity_score")
