"""Persist normalized Stage 0 keyword-to-source-agent evidence.

Revision ID: 0148_rrugc_keyword_agent_origins
Revises: 0147_rrugc_research_intelligence
Only additive tables; existing keyword metrics and job references are untouched.
"""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0148_rrugc_keyword_agent_origins"
down_revision = "0147_rrugc_research_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_keyword_agent_origins",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("keyword_normalized", sa.String(500), nullable=False),
        sa.Column("agent_key", sa.String(100), nullable=False),
        sa.Column("agent_name", sa.String(160), nullable=False),
        sa.Column("source_platform", sa.String(32), nullable=False),
        sa.Column("source_channel", sa.String(80), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(2048)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "keyword_normalized", "agent_key", "evidence_hash",
            name="uq_rrugc_keyword_agent_evidence",
        ),
    )
    op.create_index(
        "ix_rrugc_keyword_agent_origin_lookup", "rrugc_keyword_agent_origins",
        ["tenant_id", "keyword_normalized"],
    )
    # Backfill attribution ONLY for provably sourced, already approved
    # Agent-Reach research. Legacy Pinterest keyword rows have no agent ID;
    # do not retroactively misattribute them.
    bind = op.get_bind()
    existing = bind.execute(sa.text("""
        SELECT r.tenant_id, v.keyword_normalized, r.created_by_user_id,
               r.source, r.channel, r.url, r.reviewed_at
        FROM rrugc_research_signals AS r
        JOIN rrugc_keyword_volumes AS v
          ON v.tenant_id = r.tenant_id
         AND v.keyword_normalized = lower(trim(r.keyword))
        WHERE r.status = 'approved'
          AND r.keyword IS NOT NULL
          AND r.channel = 'auto-discover'
          AND r.created_by_user_id LIKE 'scout-agent:%'
    """)).mappings().all()
    origin = sa.table(
        "rrugc_keyword_agent_origins",
        *[sa.column(name) for name in (
            "id", "tenant_id", "keyword_normalized", "agent_key", "agent_name",
            "source_platform", "source_channel", "evidence_hash", "source_url", "first_seen_at",
        )],
    )
    for row in existing:
        bind.execute(origin.insert().values(
            id=str(uuid4()), tenant_id=row["tenant_id"],
            keyword_normalized=row["keyword_normalized"],
            agent_key="scout:" + row["created_by_user_id"].split(":", 1)[1],
            agent_name="Agent-Reach Auto Discover",
            source_platform=row["source"], source_channel=row["channel"],
            source_url=row["url"],
            evidence_hash=sha256((row["source"] + "|" + row["url"]).encode()).hexdigest(),
            first_seen_at=row["reviewed_at"] or datetime.now(timezone.utc),
        ))


def downgrade() -> None:
    # Destructive for provenance only. Do not execute against production.
    op.drop_index("ix_rrugc_keyword_agent_origin_lookup", table_name="rrugc_keyword_agent_origins")
    op.drop_table("rrugc_keyword_agent_origins")
