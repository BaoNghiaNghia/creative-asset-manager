from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.processing.model import ProcessingJobModel
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.video_search.model import VideoAnalysisRunModel

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


def _parent_ids(source: SourceAssetModel) -> tuple[str, ...]:
    metadata = source.source_metadata if isinstance(source.source_metadata, dict) else {}
    values = metadata.get("parents")
    if not isinstance(values, list):
        values = [metadata.get("parent_id")]
    return tuple(str(value) for value in values if value)


def _video_locations(
    session: Session,
    tenant_id: str,
    sources: list[SourceAssetModel],
    external_sources: dict[str, ExternalSourceModel],
) -> dict[str, str]:
    """Build safe, database-backed source folder paths for the current page."""
    nodes = {
        (source.external_source_id, source.external_asset_id): source
        for source in sources
    }
    source_ids = {source.external_source_id for source in sources}
    frontier = {
        parent_id
        for source in sources
        for parent_id in _parent_ids(source)
    }
    visited = set(frontier)

    # Resolve only the ancestors needed by the visible page, never by calling Drive.
    for _depth in range(16):
        if not frontier:
            break
        rows = list(session.scalars(select(SourceAssetModel).where(
            SourceAssetModel.tenant_id == tenant_id,
            SourceAssetModel.external_source_id.in_(source_ids),
            SourceAssetModel.external_asset_id.in_(frontier),
            SourceAssetModel.deleted_at.is_(None),
        )))
        next_frontier: set[str] = set()
        for row in rows:
            nodes[(row.external_source_id, row.external_asset_id)] = row
            for parent_id in _parent_ids(row):
                if parent_id not in visited:
                    visited.add(parent_id)
                    next_frontier.add(parent_id)
        frontier = next_frontier

    locations: dict[str, str] = {}
    for source in sources:
        parts: list[str] = []
        current = source
        seen: set[str] = set()
        for _depth in range(16):
            parents = _parent_ids(current)
            if not parents:
                break
            parent_id = parents[0]
            if parent_id in seen:
                break
            seen.add(parent_id)
            parent = nodes.get((source.external_source_id, parent_id))
            if parent is None:
                break
            if parent.filename:
                parts.append(parent.filename)
            current = parent
        root_name = (external_sources.get(source.external_source_id).display_name
            if external_sources.get(source.external_source_id) is not None
            else None) or "Google Drive"
        locations[source.id] = " / ".join([root_name, *reversed(parts)])
    return locations


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

    async def snapshot(self, tenant_id: str, *, video_page: int = 1, video_page_size: int = 25) -> dict:
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
        ordered_video = sorted(by_type[VIDEO_JOB_TYPE], key=lambda row: _as_utc(row.updated_at) or now, reverse=True)
        video_page = max(1, video_page)
        video_page_size = min(100, max(1, video_page_size))
        offset = (video_page - 1) * video_page_size
        page_jobs = ordered_video[offset:offset + video_page_size]
        page_sources = [
            self.session.get(SourceAssetModel, job.entity_id)
            for job in page_jobs
            if job.entity_type == "source_asset"
        ]
        page_sources = [source for source in page_sources if source is not None]
        external_sources = {
            source.external_source_id: self.session.get(ExternalSourceModel, source.external_source_id)
            for source in page_sources
        }
        locations = _video_locations(
            self.session,
            tenant_id,
            page_sources,
            {source_id: external for source_id, external in external_sources.items() if external is not None},
        )
        source_ids = {source.id for source in page_sources}
        latest_runs: dict[str, VideoAnalysisRunModel] = {}
        if source_ids:
            runs = self.session.scalars(
                select(VideoAnalysisRunModel)
                .where(
                    VideoAnalysisRunModel.tenant_id == tenant_id,
                    VideoAnalysisRunModel.source_asset_id.in_(source_ids),
                )
                .order_by(VideoAnalysisRunModel.updated_at.desc(), VideoAnalysisRunModel.id.desc())
            )
            for run in runs:
                latest_runs.setdefault(run.source_asset_id, run)
        recent_video = []
        for job in page_jobs:
            source = self.session.get(SourceAssetModel, job.entity_id) if job.entity_type == "source_asset" else None
            external = external_sources.get(source.external_source_id) if source is not None else None
            run = latest_runs.get(source.id) if source is not None else None
            thumbnail_url = None
            if source is not None and external is not None and external.source_type == "google_drive":
                thumbnail_url = f"/api/explorer/thumbnail/{source.external_asset_id}?provider=google-drive&external_source_id={source.external_source_id}&fallback=video"
            recent_video.append({
                "job_id": job.id, "source_asset_id": job.entity_id,
                "filename": source.filename if source is not None else None,
                "location": locations.get(source.id) if source is not None else None,
                "thumbnail_url": thumbnail_url,
                "completed_chunks": run.completed_chunks if run is not None else 0,
                "total_chunks": run.total_chunks if run is not None else 0,
                "status": job.status, "attempt_count": job.attempt_count,
                "max_attempts": job.max_attempts,
                "updated_at": (_as_utc(job.updated_at) or now).isoformat(),
                "error_code": job.last_error_code,
            })
        return {
            "image": image,
            "video": video,
            "video_indexing": indexing,
            "pipeline": {"image": [image], "video": [video, indexing]},
            "recent_video": {"page": video_page, "page_size": video_page_size, "total": len(ordered_video), "items": recent_video},
            "workers": [
                worker("image", (IMAGE_JOB_TYPE,), image_probe),
                worker("video", (VIDEO_JOB_TYPE, VIDEO_INDEX_JOB_TYPE), video_probe),
            ],
            "generated_at": now.isoformat(),
        }
