from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.processing.model import ProcessingJobModel

IMAGE_JOB_TYPE = "asset_analyze"
VIDEO_JOB_TYPE = "video_analyze"
VIDEO_INDEX_JOB_TYPE = "video_search_index"
_RUNNING = {"processing", "claimed", "running"}
_QUEUED = {"pending", "queued", "retry"}
_TERMINAL = {"completed", "failed"}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _stage(key: str, label: str, rows: list[ProcessingJobModel], now: datetime) -> dict:
    counts = Counter(row.status for row in rows)
    waiting_rate_limit = sum(
        row.status in _QUEUED
        and row.last_error_code in {"ai_model_rate_limited", "rate_limited"}
        and (retry_at := _as_utc(row.next_attempt_at)) is not None
        and retry_at > now
        for row in rows
    )
    eligible_now = sum(
        row.status in _QUEUED
        and ((retry_at := _as_utc(row.next_attempt_at)) is None or retry_at <= now)
        for row in rows
    )
    return {
        "key": key,
        "label": label,
        "queued": sum(counts[state] for state in _QUEUED),
        "eligible_now": eligible_now,
        "running": sum(counts[state] for state in _RUNNING),
        "completed": counts["completed"],
        "failed": counts["failed"],
        "waiting_rate_limit": waiting_rate_limit,
    }


async def _probe_worker(base_url: str, timeout: float) -> dict:
    # Only return health state; URLs and transport errors are deliberately not exposed.
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            live, ready = await __import__("asyncio").gather(
                client.get(base_url.rstrip("/") + "/live"),
                client.get(base_url.rstrip("/") + "/ready"),
            )
        return {"live": live.status_code == 200, "ready": ready.status_code == 200, "probe": "available"}
    except (httpx.HTTPError, ValueError):
        return {"live": None, "ready": None, "probe": "unavailable"}


class MediaDashboardService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    async def snapshot(self, tenant_id: str) -> dict:
        now = datetime.now(timezone.utc)
        rows = list(self.session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type.in_((IMAGE_JOB_TYPE, VIDEO_JOB_TYPE, VIDEO_INDEX_JOB_TYPE)),
        )))
        by_type = {job_type: [row for row in rows if row.job_type == job_type] for job_type in (IMAGE_JOB_TYPE, VIDEO_JOB_TYPE, VIDEO_INDEX_JOB_TYPE)}
        image = _stage(IMAGE_JOB_TYPE, "Image analysis", by_type[IMAGE_JOB_TYPE], now)
        video = _stage(VIDEO_JOB_TYPE, "Video analysis", by_type[VIDEO_JOB_TYPE], now)
        indexing = _stage(VIDEO_INDEX_JOB_TYPE, "Video indexing", by_type[VIDEO_INDEX_JOB_TYPE], now)
        image["state"] = "waiting_rate_limit" if image["waiting_rate_limit"] and not image["running"] else ("running" if image["running"] else "idle")
        image_probe, video_probe = await __import__("asyncio").gather(
            _probe_worker(self.settings.IMAGE_WORKER_HEALTH_URL, self.settings.HEALTHCHECK_TIMEOUT_SECONDS),
            _probe_worker(self.settings.VIDEO_WORKER_HEALTH_URL, self.settings.HEALTHCHECK_TIMEOUT_SECONDS),
        )
        def worker(role: str, job_types: tuple[str, ...], probe: dict) -> dict:
            active = [row for row in rows if row.job_type in job_types and row.status in _RUNNING]
            claims = [row.claimed_at for row in rows if row.job_type in job_types and row.claimed_at]
            return {
                "role": role,
                **probe,
                "active_jobs": len(active),
                "current_job_type": active[0].job_type if active else None,
                "last_successful_claim_at": max(_as_utc(value) for value in claims).isoformat() if claims else None,
            }
        return {
            "image": image,
            "video": video,
            "video_indexing": indexing,
            "pipeline": {"image": [image], "video": [video, indexing]},
            "workers": [
                worker("image", (IMAGE_JOB_TYPE,), image_probe),
                worker("video", (VIDEO_JOB_TYPE, VIDEO_INDEX_JOB_TYPE), video_probe),
            ],
            "generated_at": now.isoformat(),
        }
