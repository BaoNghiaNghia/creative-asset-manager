"""Scale RRUGC Pinterest dedupe and scout scheduling.

Revision ID: 0112_rrugc_tenant_source_key_index
Revises: 0111_rrugc_minimum_head_ratio
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "0112_rrugc_tenant_source_key_index"
down_revision = "0111_rrugc_minimum_head_ratio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_rrugc_candidate_tenant_source_key",
        "rrugc_candidates",
        ["tenant_id", "source_key"],
        unique=False,
    )
    op.create_index(
        "ix_rrugc_candidate_campaign_created",
        "rrugc_candidates",
        ["tenant_id", "campaign_id", "created_at"],
        unique=False,
    )
    op.add_column(
        "rrugc_candidates",
        sa.Column("diversity_signature", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_rrugc_candidate_campaign_diversity",
        "rrugc_candidates",
        ["tenant_id", "campaign_id", "diversity_signature", "status"],
        unique=False,
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("search_query_anchors_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column(
            "scan_failure_streak",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "rrugc_scout_runs",
        sa.Column("keyword_stats_json", sa.JSON(), nullable=True),
    )

    op.create_table(
        "rrugc_visual_fingerprints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["rrugc_candidates.tenant_id", "rrugc_candidates.id"],
            name="fk_rrugc_visual_fingerprint_candidate",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "candidate_id",
            "fingerprint",
            name="uq_rrugc_visual_fingerprint_candidate",
        ),
    )
    op.create_index(
        "ix_rrugc_visual_fingerprint_tenant",
        "rrugc_visual_fingerprints",
        ["tenant_id", "fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_rrugc_visual_fingerprint_campaign",
        "rrugc_visual_fingerprints",
        ["tenant_id", "campaign_id", "candidate_id"],
        unique=False,
    )

    # Backfill the dedicated lookup table from already analyzed candidates so
    # production keeps near-duplicate protection immediately after migration.
    bind = op.get_bind()
    campaigns = sa.table(
        "rrugc_campaigns",
        sa.column("id", sa.String(length=36)),
        sa.column("query", sa.String(length=500)),
        sa.column("search_query_anchors_json", sa.JSON()),
    )
    for row in bind.execute(
        sa.select(campaigns.c.id, campaigns.c.query)
    ).mappings():
        query = str(row["query"] or "").strip()
        if query:
            bind.execute(
                sa.update(campaigns)
                .where(campaigns.c.id == row["id"])
                .values(search_query_anchors_json=[query])
            )

    candidates = sa.table(
        "rrugc_candidates",
        sa.column("id", sa.String(length=36)),
        sa.column("tenant_id", sa.String(length=255)),
        sa.column("campaign_id", sa.String(length=36)),
        sa.column("status", sa.String(length=32)),
        sa.column("ai_signal_json", sa.JSON()),
        sa.column("diversity_signature", sa.String(length=255)),
    )
    fingerprints = sa.table(
        "rrugc_visual_fingerprints",
        sa.column("id", sa.String(length=36)),
        sa.column("tenant_id", sa.String(length=255)),
        sa.column("campaign_id", sa.String(length=36)),
        sa.column("candidate_id", sa.String(length=36)),
        sa.column("fingerprint", sa.String(length=64)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    batch: list[dict] = []
    rows = bind.execute(
        sa.select(
            candidates.c.id,
            candidates.c.tenant_id,
            candidates.c.campaign_id,
            candidates.c.ai_signal_json,
        ).where(
            candidates.c.ai_signal_json.is_not(None),
            candidates.c.status.in_((
                "approved",
                "import_queued",
                "importing",
                "drive_ready",
            )),
        )
    )
    for row in rows.mappings():
        signal = row["ai_signal_json"]
        if not isinstance(signal, dict):
            continue
        diversity_signature = signal.get("diversity_signature")
        if isinstance(diversity_signature, str) and diversity_signature.strip():
            bind.execute(
                sa.update(candidates)
                .where(candidates.c.id == row["id"])
                .values(diversity_signature=diversity_signature.strip()[:255])
            )
        values = signal.get("visual_fingerprints")
        if not isinstance(values, list):
            continue
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            clean = value.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            batch.append({
                "id": str(uuid4()),
                "tenant_id": row["tenant_id"],
                "campaign_id": row["campaign_id"],
                "candidate_id": row["id"],
                "fingerprint": clean,
                "created_at": datetime.now(timezone.utc),
            })
            if len(batch) >= 1000:
                bind.execute(sa.insert(fingerprints), batch)
                batch.clear()
    if batch:
        bind.execute(sa.insert(fingerprints), batch)


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_visual_fingerprint_campaign",
        table_name="rrugc_visual_fingerprints",
    )
    op.drop_index(
        "ix_rrugc_visual_fingerprint_tenant",
        table_name="rrugc_visual_fingerprints",
    )
    op.drop_table("rrugc_visual_fingerprints")
    op.drop_column("rrugc_scout_runs", "keyword_stats_json")
    op.drop_column("rrugc_campaigns", "scan_failure_streak")
    op.drop_column("rrugc_campaigns", "search_query_anchors_json")
    op.drop_index(
        "ix_rrugc_candidate_campaign_diversity",
        table_name="rrugc_candidates",
    )
    op.drop_column("rrugc_candidates", "diversity_signature")
    op.drop_index(
        "ix_rrugc_candidate_campaign_created",
        table_name="rrugc_candidates",
    )
    op.drop_index(
        "ix_rrugc_candidate_tenant_source_key",
        table_name="rrugc_candidates",
    )
