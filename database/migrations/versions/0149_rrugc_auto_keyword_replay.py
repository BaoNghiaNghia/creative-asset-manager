"""Replay validated Agent-Reach quote candidates into Stage 0 without a new search.

Revision ID: 0149_rrugc_auto_keyword_replay
Revises: 0148_rrugc_keyword_agent_origins

Historical public search results were synced into Research Inbox before
automatic extraction existed. This migration promotes only conservative,
multiword text candidates from the paired dedicated VPS Agent-Reach identity.
Remaining ambiguous signals are untouched for human review. No paid API calls,
new DDL, or tenant-wide access permissions are involved.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

revision = "0149_rrugc_auto_keyword_replay"
down_revision = "0148_rrugc_keyword_agent_origins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
    from app.modules.realistic_review_ugc.research_discovery import AGENT_NAME
    from app.modules.realistic_review_ugc.research_intelligence import (
        backfill_auto_discover_candidates,
    )

    conn = op.get_bind()
    # A single Alembic-owned transaction covers all tenant-scoped insertions,
    # review state updates and keyword-Agent origin records.
    with Session(bind=conn, autoflush=False, expire_on_commit=False) as session:
        tenants = session.scalars(sa.select(RrugcScoutAgentModel.tenant_id).where(
            RrugcScoutAgentModel.name == AGENT_NAME,
        ).distinct()).all()
        for tenant_id in tenants:
            backfill_auto_discover_candidates(
                session, tenant_id=tenant_id, dry_run=False, commit=False
            )
        session.flush()


def downgrade() -> None:
    # Do not discard user-visible Stage 0 keyword data or attribution during a
    # rollback. This data replay is intentionally forward-only/idempotent.
    pass
