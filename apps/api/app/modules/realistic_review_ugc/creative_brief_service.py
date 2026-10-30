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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.creative_brief import CreativeBrief, generate_brief
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel,
)

PROMPT_VERSION = "meaning-first-v1"
JOB_TYPE = "rrugc_creative_brief_generate"


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
) -> tuple[str, bool]:
    settings = settings or get_settings()
    if not settings.RRUGC_CREATIVE_BRIEF_ENABLED:
        raise RuntimeError("creative brief feature disabled")
    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ))
    if keyword is None:
        raise LookupError("picked keyword not found")
    digest = _digest(keyword.keyword)
    row = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.tenant_id == tenant_id,
        RrugcKeywordCreativeBriefModel.keyword_id == keyword_id,
    ).with_for_update())
    if row and row.keyword_sha256 == digest and row.prompt_version == PROMPT_VERSION:
        if row.status in ("queued", "running", "completed") or not retry:
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
        priority=5, max_attempts=2, provider_key=None, provider_scope=None,
    )
    row.processing_job_id = task.id
    session.flush()
    return "queued", True


def queue_picked_backfill(
    session: Session, *, tenant_id: str, limit: int = 500, offset: int = 0,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("invalid batch window")
    # Read only picked rows. Skip anything already queued or completed;
    # explicit retries are not silently performed for failed jobs.
    keywords = list(session.scalars(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ).order_by(RrugcKeywordVolumeModel.picked_at.desc().nullslast(),
               RrugcKeywordVolumeModel.id.asc()).offset(offset).limit(limit)))
    created = 0
    for keyword in keywords:
        _status, was_created = queue_brief(
            session, tenant_id=tenant_id, keyword_id=keyword.id, settings=settings,
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

        # Three image workers may claim simultaneously. A non-blocking global
        # file lock keeps Codex text-only jobs single-flight on this VPS.
        # The staging root is shared by all systemd worker instances; /tmp may
        # be service-private, so it cannot provide a cross-worker lock.
        lock_path = Path(self.settings.IMAGE_GENERATION_STAGING_ROOT) / ".creative-brief.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return DeferredJobOutcome(
                    reason_code="creative_brief_busy",
                    reason_message="Another Creative Brief is running.",
                    retry_at=_utcnow() + timedelta(seconds=30),
                )
            try:
                return self._execute_locked(context)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

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
