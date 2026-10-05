"""Add source Pinterest media and clean RRUGC Stage 0 keywords.

Revision ID: 0131_rrugc_keyword_source_media
Revises: 0130_rrugc_keyword_volume
"""
from __future__ import annotations

import ast
import json
import re

from alembic import op
import sqlalchemy as sa


revision = "0131_rrugc_keyword_source_media"
down_revision = "0130_rrugc_keyword_volume"
branch_labels = None
depends_on = None

_MIN_KEYWORD_CHARS = 5


def _clean_keyword(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        parsed: object = None
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(text)
                break
            except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
                continue
        if isinstance(parsed, dict):
            text = str(parsed.get("text") or parsed.get("phrase") or "")
        else:
            match = re.search(
                r"""['"](?:text|phrase)['"]\s*:\s*(['"])(.*?)\1\s*(?:,\s*['"][^'"]+['"]\s*:|})""",
                text,
                flags=re.DOTALL,
            )
            if match:
                text = match.group(2)
    return re.sub(r"\s+", " ", text).strip(" \t\r\n\"'“”")


def _meaningful_length(value: str) -> int:
    return sum(1 for char in value if char.isalnum())


def _cleanup_existing_keywords() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, tenant_id, keyword, keyword_normalized
            FROM rrugc_keyword_volumes
            """
        )
    ).mappings().all()

    for row in rows:
        clean = _clean_keyword(row["keyword"])
        if _meaningful_length(clean) < _MIN_KEYWORD_CHARS:
            bind.execute(
                sa.text("DELETE FROM rrugc_keyword_volumes WHERE id = :id"),
                {"id": row["id"]},
            )
            continue

        normalized = clean.casefold()
        if (
            clean == str(row["keyword"] or "")
            and normalized == str(row["keyword_normalized"] or "")
        ):
            continue

        duplicate_id = bind.execute(
            sa.text(
                """
                SELECT id
                FROM rrugc_keyword_volumes
                WHERE tenant_id = :tenant_id
                  AND keyword_normalized = :keyword_normalized
                  AND id <> :id
                LIMIT 1
                """
            ),
            {
                "tenant_id": row["tenant_id"],
                "keyword_normalized": normalized,
                "id": row["id"],
            },
        ).scalar()
        if duplicate_id is not None:
            bind.execute(
                sa.text("DELETE FROM rrugc_keyword_volumes WHERE id = :id"),
                {"id": row["id"]},
            )
            continue

        bind.execute(
            sa.text(
                """
                UPDATE rrugc_keyword_volumes
                SET keyword = :keyword,
                    keyword_normalized = :keyword_normalized
                WHERE id = :id
                """
            ),
            {
                "keyword": clean,
                "keyword_normalized": normalized,
                "id": row["id"],
            },
        )


def upgrade() -> None:
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("source_image_url", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("source_pin_url", sa.String(length=2048), nullable=True),
    )
    _cleanup_existing_keywords()


def downgrade() -> None:
    op.drop_column("rrugc_keyword_volumes", "source_pin_url")
    op.drop_column("rrugc_keyword_volumes", "source_image_url")
