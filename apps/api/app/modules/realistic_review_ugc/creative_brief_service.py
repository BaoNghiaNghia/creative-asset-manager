"""Creative Brief lifecycle. Reuse existing Processing jobs; no ImageGen.

Only picked Stage 0 keywords are accepted; every query is tenant-scoped.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.creative_brief import CreativeBrief, generate_brief
from app.modules.realistic_review_ugc.creative_engine_v3 import SCHEMA_VERSION as V3_SCHEMA, build_v3_preview
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel,
)

PROMPT_VERSION = "meaning-first-v1"
JOB_TYPE = "rrugc_creative_brief_generate"
INTERACTIVE_BRIEF_PRIORITY = 65  # above general aged 50 / Stage 1 queued 51


def v3_enabled(settings: Settings, tenant_id: str) -> bool:
    """Fail closed unless both flags and an explicit tenant ID match."""
    return bool(
        settings.CREATIVE_ENGINE_V3_ENABLED
        and tenant_id
        and tenant_id in {
            item.strip() for item in settings.CREATIVE_ENGINE_V3_CANARY_TENANT_IDS.split(",")
            if item.strip()
        }
    )


def get_v3_preview(session: Session, *, tenant_id: str, keyword_id: str) -> dict:
    """Read-only projection of existing picked-keyword V1, no DB or AI writes."""
    detail = get_brief(session, tenant_id=tenant_id, keyword_id=keyword_id)
    response = {
        "schema_version": V3_SCHEMA,
        "keyword_id": detail["keyword_id"],
        "keyword_exact": detail["keyword"],
        "source_status": detail["status"],
    }
    if detail["status"] != "completed" or detail["brief"] is None:
        return {**response, "planner_status": "v1_brief_not_ready", "concepts": [],
                "preview_only": True, "job_created": False, "can_generate_artwork": False}
    return {**response, **build_v3_preview(
        keyword=detail["keyword"], brief_json=detail["brief"],
        source_prompt_version=detail["prompt_version"],
    )}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digest(keyword: str) -> str:
    return hashlib.sha256(keyword.encode("utf-8")).hexdigest()


def brief_summary(row: RrugcKeywordCreativeBriefModel | None) -> dict | None:
    if row is None:
        return None
    content = row.brief_json if row.status == "completed" and isinstance(row.brief_json, dict) else None
    return {
        "status": row.status,
        "strategy": content.get("strategy") if content else None,
        "meaning_emotion": content.get("meaning_emotion") if content else None,
        "creative_hook": content.get("creative_hook") if content else None,
        "concept_count": len(content.get("concepts") or ()) if content else 0,
    }


def get_brief(session: Session, *, tenant_id: str, keyword_id: str) -> dict:
    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ))
    if keyword is None:
        raise LookupError("picked keyword not found")
    row = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.tenant_id == tenant_id,
        RrugcKeywordCreativeBriefModel.keyword_id == keyword_id,
    ))
    stale = bool(row and (row.keyword_sha256 != _digest(keyword.keyword)
                          or row.prompt_version != PROMPT_VERSION))
    return {
        "keyword_id": keyword.id,
        "keyword": keyword.keyword,
        "status": "stale" if stale else row.status if row else "not_analyzed",
        "brief": None if stale or not row or row.status != "completed" else row.brief_json,
        "error_code": row.last_error_code if row and row.status == "failed" else None,
        "prompt_version": row.prompt_version if row else None,
        "updated_at": row.updated_at.isoformat() if row else None,
    }


def queue_brief(
    session: Session, *, tenant_id: str, keyword_id: str,
    settings: Settings | None = None, retry: bool = False,
    priority: int = 5,
) -> tuple[str, bool]:
    settings = settings or get_settings()
    if not settings.RRUGC_CREATIVE_BRIEF_ENABLED:
        raise RuntimeError("creative brief feature disabled")
    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ).with_for_update())
    if keyword is None:
        raise LookupError("picked keyword not found")
    digest = _digest(keyword.keyword)
    row = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.tenant_id == tenant_id,
        RrugcKeywordCreativeBriefModel.keyword_id == keyword_id,
    ).with_for_update())
    if row and row.keyword_sha256 == digest and row.prompt_version == PROMPT_VERSION:
        if row.status in ("queued", "running", "completed") or not retry:
            # An explicit user click should run ahead of the background backfill.
            # Promote only an existing pending job: no duplicate generation and
            # no mutation of running, completed or other-tenant jobs.
            if row.status == "queued" and priority >= INTERACTIVE_BRIEF_PRIORITY and row.processing_job_id:
                session.execute(
                    update(ProcessingJobModel)
                    .where(
                        ProcessingJobModel.id == row.processing_job_id,
                        ProcessingJobModel.tenant_id == tenant_id,
                        ProcessingJobModel.job_type == JOB_TYPE,
                        ProcessingJobModel.status.in_(("pending", "retry")),
                        ProcessingJobModel.priority < priority,
                    ).values(priority=priority).execution_options(synchronize_session=False)
                )
            return row.status, False
    if row is None:
        row = RrugcKeywordCreativeBriefModel(
            id=str(uuid4()), tenant_id=tenant_id, keyword_id=keyword_id,
            keyword_sha256=digest, prompt_version=PROMPT_VERSION,
            status="queued",
        )
        session.add(row)
        session.flush()
    else:
        row.keyword_sha256 = digest
        row.prompt_version = PROMPT_VERSION
        row.status = "queued"
        row.brief_json = None
        row.completed_at = None
        row.last_error_code = None

    # A new work item is always idempotent at the database row level. The
    # unique processing key contains a nonce only for explicit retries.
    task = ProcessingRepository(session, settings).create_job(
        tenant_id=tenant_id,
        job_type=JOB_TYPE, entity_type="rrugc_keyword_creative_brief",
        entity_id=row.id,
        idempotency_key=f"creative-brief:{row.id}:{PROMPT_VERSION}:{uuid4().hex}",
        payload={"keyword_id":keyword_id, "prompt_version": PROMPT_VERSION},
        priority=priority, max_attempts=2, provider_key=None, provider_scope=None,
    )
    row.processing_job_id = task.id
    session.flush()
    return "queued", True


def queue_picked_backfill(
    session: Session, *, tenant_id: str, limit: int = 500, offset: int = 0,
    settings: Settings | None = None, retry_failed: bool = False,
) -> dict:
    settings = settings or get_settings()
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("invalid batch window")
    # Read only picked rows. Keep completed and active briefs. Failed
    # analyses may be retried once by an explicitly requested backfill
    # (never by a recurring/default sweep).
    keywords = list(session.scalars(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ).order_by(RrugcKeywordVolumeModel.id.asc()).offset(offset).limit(limit)))
    created = 0
    for keyword in keywords:
        _status, was_created = queue_brief(
            session, tenant_id=tenant_id, keyword_id=keyword.id, settings=settings,
            retry=retry_failed,
        )
        if was_created:
            created += 1
    session.commit()
    return {"examined": len(keywords), "queued": created, "skipped": len(keywords) - created}


class CreativeBriefGenerateHandler:
    """Bounded single-flight text generation; no ImageGen, no prompt logs."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        if not self.settings.RRUGC_CREATIVE_BRIEF_ENABLED:
            return JobHandlerResult.non_retryable("creative_brief_disabled", "Creative Brief is disabled.")

        # Use two bounded, cross-process Codex text slots rather than a
        # single global lock for hundreds of picked keywords. Keep the original
        # lock path as slot zero so an overlapping older worker remains safe.
        # This is file-lock coordination only: no new worker or DB schema.
        # Never use /tmp: systemd may give each worker a private /tmp.
        lock_root = Path(self.settings.IMAGE_GENERATION_STAGING_ROOT)
        lock_root.mkdir(parents=True, exist_ok=True)
        limit = max(1, min(2, int(getattr(self.settings, "RRUGC_CREATIVE_BRIEF_MAX_CONCURRENCY", 1))))
        for slot in range(limit):
            lock_path = lock_root / (".creative-brief.lock" if slot == 0 else ".creative-brief.slot-1.lock")
            with lock_path.open("a+b") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    return self._execute_locked(context)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return DeferredJobOutcome(
            reason_code="creative_brief_busy",
            reason_message="Both Creative Brief Codex slots are busy; retry later.",
            retry_at=_utcnow() + timedelta(seconds=60),
        )

    def _set_status(self, context: JobHandlerContext, state: str, error_code: str | None = None) -> None:
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
                RrugcKeywordCreativeBriefModel.id == context.job.entity_id,
                RrugcKeywordCreativeBriefModel.tenant_id == context.job.tenant_id,
            ))
            if row is not None and row.processing_job_id == context.job.id:
                row.status = state
                row.last_error_code = error_code
                session.commit()

    def _execute_locked(self, context: JobHandlerContext) -> JobHandlerResult:
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
                RrugcKeywordCreativeBriefModel.id == context.job.entity_id,
                RrugcKeywordCreativeBriefModel.tenant_id == context.job.tenant_id,
            ))
            if row is None or row.processing_job_id != context.job.id:
                return JobHandlerResult.non_retryable("creative_brief_stale", "Creative Brief job is stale.")
            keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
                RrugcKeywordVolumeModel.id == row.keyword_id,
                RrugcKeywordVolumeModel.tenant_id == row.tenant_id,
                RrugcKeywordVolumeModel.picked.is_(True),
            ))
            if keyword is None or row.prompt_version != PROMPT_VERSION or row.keyword_sha256 != _digest(keyword.keyword):
                row.status = "failed"
                row.last_error_code = "creative_brief_source_changed"
                session.commit()
                return JobHandlerResult.non_retryable("creative_brief_source_changed", "Keyword has changed.")
            if row.status == "completed":
                return JobHandlerResult.completed()
            row.status = "running"
            row.last_error_code = None
            phrase = keyword.keyword
            session.commit()
        try:
            brief: CreativeBrief = asyncio.run(generate_brief(
                phrase,
                codex_binary=self.settings.CODEX_IMAGE_BINARY,
                codex_home=self.settings.CODEX_IMAGE_HOME,
                model=self.settings.RRUGC_CREATIVE_BRIEF_MODEL.strip() or None,
                timeout_seconds=min(600, max(5, self.settings.RRUGC_CREATIVE_BRIEF_TIMEOUT_SECONDS)),
            ))
        except TimeoutError:
            self._set_status(context, "queued", "creative_brief_timeout")
            return JobHandlerResult.retryable("creative_brief_timeout", "Codex text generation timed out.")
        except (RuntimeError, ValueError, OSError):
            self._set_status(context, "failed", "creative_brief_generation_failed")
            return JobHandlerResult.non_retryable(
                "creative_brief_generation_failed", "Codex text generation or schema validation failed."
            )

        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
                RrugcKeywordCreativeBriefModel.id == context.job.entity_id,
                RrugcKeywordCreativeBriefModel.tenant_id == context.job.tenant_id,
            ).with_for_update())
            keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
                RrugcKeywordVolumeModel.id == row.keyword_id,
                RrugcKeywordVolumeModel.tenant_id == row.tenant_id,
                RrugcKeywordVolumeModel.picked.is_(True),
            )) if row else None
            if (row is None or keyword is None or row.processing_job_id != context.job.id
                    or row.keyword_sha256 != _digest(keyword.keyword)
                    or row.prompt_version != PROMPT_VERSION):
                return JobHandlerResult.non_retryable(
                    "creative_brief_source_changed", "Keyword changed during generation."
                )
            row.brief_json = brief.model_dump(mode="json")
            row.status = "completed"
            row.last_error_code = None
            row.completed_at = _utcnow()
            session.commit()
        return JobHandlerResult.completed()