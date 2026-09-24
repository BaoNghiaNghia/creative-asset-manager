"""Backfill missing video Asset/SourceAsset links for Public Review.

Revision ID: 0091_video_asset_link_backfill
Revises: 0090_creative_pipeline_gpt_skills
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0091_video_asset_link_backfill"
down_revision = "0090_creative_pipeline_gpt_skills"
branch_labels = None
depends_on = None

_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}


def _is_video(filename: str | None, mime_type: str | None) -> bool:
    return str(mime_type or "").lower().startswith("video/") or Path(str(filename or "")).suffix.lower() in _VIDEO_EXTENSIONS


def upgrade() -> None:
    bind = op.get_bind()
    candidates = bind.execute(
        sa.text(
            """
            SELECT sa.id, sa.tenant_id, sa.provider_checksum, sa.provider_version,
                   sa.filename, sa.mime_type, sa.size_bytes
            FROM source_assets AS sa
            LEFT JOIN asset_source_links AS asl
              ON asl.tenant_id = sa.tenant_id
             AND asl.source_asset_id = sa.id
            WHERE sa.deleted_at IS NULL
              AND sa.is_folder = false
              AND asl.id IS NULL
              AND length(COALESCE(sa.provider_checksum, '')) = 64
            ORDER BY sa.id
            """
        )
    ).mappings().all()

    now = datetime.now(timezone.utc)
    for row in candidates:
        if not _is_video(row["filename"], row["mime_type"]):
            continue
        raw_checksum = str(row["provider_checksum"] or "").strip()
        if not _SHA256_RE.fullmatch(raw_checksum):
            continue
        checksum = raw_checksum.lower()
        asset_id = bind.execute(
            sa.text(
                """
                SELECT id
                FROM assets
                WHERE tenant_id = :tenant_id
                  AND content_hash = :content_hash
                LIMIT 1
                """
            ),
            {"tenant_id": row["tenant_id"], "content_hash": checksum},
        ).scalar_one_or_none()
        if asset_id is None:
            asset_id = str(uuid4())
            bind.execute(
                sa.text(
                    """
                    INSERT INTO assets
                        (id, tenant_id, content_hash, analysis_image_hash, mime_type,
                         size_bytes, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :content_hash, NULL, :mime_type,
                         :size_bytes, :created_at, :updated_at)
                    """
                ),
                {
                    "id": asset_id,
                    "tenant_id": row["tenant_id"],
                    "content_hash": checksum,
                    "mime_type": row["mime_type"],
                    "size_bytes": row["size_bytes"],
                    "created_at": now,
                    "updated_at": now,
                },
            )

        already_linked = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM asset_source_links
                WHERE tenant_id = :tenant_id
                  AND source_asset_id = :source_asset_id
                LIMIT 1
                """
            ),
            {
                "tenant_id": row["tenant_id"],
                "source_asset_id": row["id"],
            },
        ).scalar_one_or_none()
        if already_linked is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO asset_source_links
                        (id, tenant_id, asset_id, source_asset_id, created_at)
                    VALUES
                        (:id, :tenant_id, :asset_id, :source_asset_id, :created_at)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": row["tenant_id"],
                    "asset_id": asset_id,
                    "source_asset_id": row["id"],
                    "created_at": now,
                },
            )

        bind.execute(
            sa.text(
                """
                UPDATE source_assets
                SET hashed_provider_checksum = provider_checksum,
                    hashed_provider_version = provider_version
                WHERE tenant_id = :tenant_id
                  AND id = :source_asset_id
                """
            ),
            {
                "tenant_id": row["tenant_id"],
                "source_asset_id": row["id"],
            },
        )


def downgrade() -> None:
    # Data repair is intentionally irreversible: removing the canonical links on
    # downgrade would re-introduce the Public Review visibility bug.
    pass
