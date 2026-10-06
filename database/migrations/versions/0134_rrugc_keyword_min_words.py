"""Require at least two words for RRUGC Stage 0 keywords.

Revision ID: 0134_rrugc_keyword_min_words
Revises: 0133_rrugc_stage3_review_copy
"""
from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "0134_rrugc_keyword_min_words"
down_revision = "0133_rrugc_stage3_review_copy"
branch_labels = None
depends_on = None

_MIN_WORDS = 2
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _word_count(value: object) -> int:
    return len(_WORD_RE.findall(str(value or "")))


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, keyword FROM rrugc_keyword_volumes")
    ).mappings().all()

    invalid_ids = [
        row["id"]
        for row in rows
        if _word_count(row["keyword"]) < _MIN_WORDS
    ]
    if not invalid_ids:
        return

    bind.execute(
        sa.text(
            "DELETE FROM rrugc_keyword_volumes "
            "WHERE id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": invalid_ids},
    )


def downgrade() -> None:
    # Deleted one-word keywords are intentionally not recreated.
    pass
