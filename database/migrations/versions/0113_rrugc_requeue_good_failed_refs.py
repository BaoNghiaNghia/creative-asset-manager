"""Requeue failed RRUGC references manually marked good.

Revision ID: 0113_rrugc_requeue_good_failed_refs
Revises: 0112_rrugc_tenant_source_key_index
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "0113_rrugc_requeue_good_failed_refs"
down_revision = "0112_rrugc_tenant_source_key_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    candidates = sa.table(
        "rrugc_candidates",
        sa.column("id", sa.String(length=36)),
        sa.column("tenant_id", sa.String(length=255)),
        sa.column("campaign_id", sa.String(length=36)),
        sa.column("status", sa.String(length=32)),
        sa.column("analysis_revision", sa.Integer()),
        sa.column("ai_signal_json", sa.JSON()),
        sa.column("ai_manual_label", sa.String(length=16)),
        sa.column("last_error_code", sa.String(length=100)),
        sa.column("reject_reason", sa.String(length=100)),
    )
    jobs = sa.table(
        "processing_jobs",
        sa.column("id", sa.String(length=36)),
        sa.column("tenant_id", sa.String(length=255)),
        sa.column("job_type", sa.String(length=64)),
        sa.column("entity_type", sa.String(length=64)),
        sa.column("entity_id", sa.String(length=255)),
        sa.column("idempotency_key", sa.String(length=512)),
        sa.column("payload_json", sa.JSON()),
        sa.column("provider_key", sa.String(length=64)),
        sa.column("provider_scope", sa.String(length=32)),
        sa.column("concurrency_accounted", sa.Boolean()),
        sa.column("status", sa.String(length=20)),
        sa.column("priority", sa.Integer()),
        sa.column("attempt_count", sa.Integer()),
        sa.column("processing_duration_ms", sa.Integer()),
        sa.column("max_attempts", sa.Integer()),
        sa.column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("cancellation_requested", sa.Boolean()),
    )

    rows = bind.execute(
        sa.select(
            candidates.c.id,
            candidates.c.tenant_id,
            candidates.c.campaign_id,
            candidates.c.analysis_revision,
            candidates.c.ai_signal_json,
            candidates.c.ai_manual_label,
        ).where(candidates.c.status == "analysis_failed")
    )

    now = datetime.now(timezone.utc)
    requeued = 0
    for row in rows.mappings():
        signal = row["ai_signal_json"]
        if not isinstance(signal, dict):
            continue
        if signal.get("reference_manual_label") != "good":
            continue
        if row["ai_manual_label"] == "ai":
            continue

        new_revision = max(1, int(row["analysis_revision"] or 0) + 1)
        signal = dict(signal)
        signal["reference_manual_pending_approval"] = True

        bind.execute(
            sa.update(candidates)
            .where(candidates.c.id == row["id"])
            .values(
                status="analysis_queued",
                analysis_revision=new_revision,
                ai_signal_json=signal,
                last_error_code=None,
                reject_reason=None,
            )
        )
        requeued += 1

        idempotency_key = f"rrugc-analyze:{row['id']}:{new_revision}"
        existing = bind.scalar(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(
                jobs.c.tenant_id == row["tenant_id"],
                jobs.c.idempotency_key == idempotency_key,
            )
        )
        if existing:
            continue

        bind.execute(
            sa.insert(jobs).values(
                id=str(uuid4()),
                tenant_id=row["tenant_id"],
                job_type="rrugc_candidate_analyze",
                entity_type="rrugc_candidate",
                entity_id=row["id"],
                idempotency_key=idempotency_key,
                payload_json={
                    "candidate_id": row["id"],
                    "campaign_id": row["campaign_id"],
                    "analysis_revision": new_revision,
                },
                provider_key="gemini",
                provider_scope="ai",
                concurrency_accounted=False,
                status="pending",
                priority=55,
                attempt_count=0,
                processing_duration_ms=0,
                max_attempts=3,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
                cancellation_requested=False,
            )
        )

    print(f"RRUGC recovery: requeued {requeued} failed REF-good candidate(s)")


def downgrade() -> None:
    # This is a one-time recovery migration. Reverting queued work after it may
    # already have run would be unsafe, so downgrade intentionally preserves it.
    pass
