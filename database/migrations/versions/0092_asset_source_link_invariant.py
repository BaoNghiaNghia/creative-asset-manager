"""Enforce one canonical asset link per source asset.

Revision ID: 0092_asset_source_link_invariant
Revises: 0091_video_asset_link_backfill
"""
from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa

revision = "0092_asset_source_link_invariant"
down_revision = "0091_video_asset_link_backfill"
branch_labels = None
depends_on = None

_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")


def _sha256(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if _SHA256_RE.fullmatch(normalized) else None


def _repair_unambiguous_duplicates(bind) -> None:
    duplicates = bind.execute(
        sa.text(
            """
            SELECT tenant_id, source_asset_id
            FROM asset_source_links
            GROUP BY tenant_id, source_asset_id
            HAVING COUNT(*) > 1
            """
        )
    ).mappings().all()

    ambiguous: list[tuple[str, str]] = []
    for duplicate in duplicates:
        rows = bind.execute(
            sa.text(
                """
                SELECT
                    asl.id,
                    asl.asset_id,
                    asl.created_at,
                    a.content_hash,
                    sa.provider_checksum,
                    sa.hashed_provider_checksum
                FROM asset_source_links AS asl
                JOIN assets AS a
                  ON a.tenant_id = asl.tenant_id
                 AND a.id = asl.asset_id
                JOIN source_assets AS sa
                  ON sa.tenant_id = asl.tenant_id
                 AND sa.id = asl.source_asset_id
                WHERE asl.tenant_id = :tenant_id
                  AND asl.source_asset_id = :source_asset_id
                ORDER BY asl.created_at DESC, asl.id DESC
                """
            ),
            {
                "tenant_id": duplicate["tenant_id"],
                "source_asset_id": duplicate["source_asset_id"],
            },
        ).mappings().all()
        if len(rows) <= 1:
            continue

        asset_ids = {str(row["asset_id"]) for row in rows}
        expected = (
            _sha256(rows[0]["provider_checksum"])
            or _sha256(rows[0]["hashed_provider_checksum"])
        )
        matching = [
            row
            for row in rows
            if expected is not None
            and _sha256(row["content_hash"]) == expected
        ]

        if len(matching) == 1:
            keep_id = matching[0]["id"]
        elif len(asset_ids) == 1:
            # Semantically identical duplicate link rows are safe to collapse.
            keep_id = rows[0]["id"]
        else:
            ambiguous.append(
                (
                    str(duplicate["tenant_id"]),
                    str(duplicate["source_asset_id"]),
                )
            )
            continue

        bind.execute(
            sa.text(
                """
                DELETE FROM asset_source_links
                WHERE tenant_id = :tenant_id
                  AND source_asset_id = :source_asset_id
                  AND id <> :keep_id
                """
            ),
            {
                "tenant_id": duplicate["tenant_id"],
                "source_asset_id": duplicate["source_asset_id"],
                "keep_id": keep_id,
            },
        )

    if ambiguous:
        raise RuntimeError(
            "asset_source_links contains ambiguous duplicate source identities; "
            f"manual repair is required for {len(ambiguous)} source asset(s)"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _repair_unambiguous_duplicates(bind)

    with op.batch_alter_table("asset_source_links") as batch_op:
        batch_op.create_unique_constraint(
            "uq_asset_source_links_tenant_source_asset",
            ["tenant_id", "source_asset_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("asset_source_links") as batch_op:
        batch_op.drop_constraint(
            "uq_asset_source_links_tenant_source_asset",
            type_="unique",
        )
