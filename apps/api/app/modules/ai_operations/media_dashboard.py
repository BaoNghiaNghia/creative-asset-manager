from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import ceil

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.redaction import redact_url_queries
from app.core.config import Settings
from app.modules.processing.model import ProcessingJobModel
from app.modules.assets.model import AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.video_search.model import VideoAnalysisChunkModel, VideoAnalysisRunModel

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


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _video_analytics(
    runs: list[VideoAnalysisRunModel],
    *,
    from_at: datetime,
    to_at: datetime,
    provider: str | None = None,
    model: str | None = None,
    processing_mode: str | None = None,
    metadata_profile: str | None = None,
    status: str | None = None,
) -> dict:
    daily: dict[str, Counter] = defaultdict(Counter)
    providers: dict[tuple[str, str | None], Counter] = defaultdict(Counter)
    provider_latencies: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    latencies: list[int] = []
    failure_counts = Counter()

    for run in runs:
        if provider and run.ai_provider != provider:
            continue
        if model and run.ai_model != model:
            continue
        if processing_mode and processing_mode != "single":
            continue
        if metadata_profile and run.metadata_profile != metadata_profile:
            continue
        if status and run.status != status:
            continue
        occurred_at = _as_utc(run.completed_at) or _as_utc(run.updated_at)
        if run.status not in _TERMINAL or occurred_at is None or not (from_at <= occurred_at < to_at):
            continue
        daily[occurred_at.date().isoformat()][run.status] += 1
        provider_key = (run.ai_provider or "unknown", run.ai_model)
        providers[provider_key]["count"] += 1
        providers[provider_key][run.status] += 1
        if run.status == "failed":
            failure_counts[run.last_error_code or "video_analysis_failed"] += 1
        started_at = _as_utc(run.started_at)
        if started_at is not None and occurred_at >= started_at:
            latency = int((occurred_at - started_at).total_seconds() * 1000)
            latencies.append(latency)
            provider_latencies[provider_key].append(latency)

    return {
        "daily": [
            {
                "date": date,
                "requested": counts["completed"] + counts["failed"],
                "completed": counts["completed"],
                "failed": counts["failed"],
                "estimated_cost_micros": 0,
                "provider_reported_cost_micros": 0,
                "reconciled_cost_micros": 0,
                "provider_estimated_cost_micros": {},
                "average_latency_ms": 0,
                "p95_latency_ms": 0,
            }
            for date, counts in sorted(daily.items())
        ],
        "providers": [
            {
                "provider": provider,
                "model": model,
                "processing_mode": "single",
                "count": counts["count"],
                "completed": counts["completed"],
                "failed": counts["failed"],
                "success_rate": counts["completed"] / counts["count"] if counts["count"] else 0,
                "average_latency_ms": (
                    sum(provider_latencies[(provider, model)]) / len(provider_latencies[(provider, model)])
                    if provider_latencies[(provider, model)] else 0
                ),
                "p95_latency_ms": _percentile(provider_latencies[(provider, model)], 0.95),
                "input_units": 0,
                "output_units": 0,
                "estimated_cost_micros": 0,
                "provider_reported_cost_micros": 0,
                "reconciled_cost_micros": 0,
                "currency": "USD",
            }
            for (provider, model), counts in sorted(
                providers.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        ],
        "failures": [
            {"source": "video_analyze", "error_code": code, "count": count}
            for code, count in sorted(failure_counts.items())
        ],
        "latency": {
            "average_ms": sum(latencies) / len(latencies) if latencies else 0,
            "p95_ms": _percentile(latencies, 0.95),
        },
        "cost_available": False,
    }


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


def _video_duration_ms(source: SourceAssetModel | None, run: VideoAnalysisRunModel | None) -> int | None:
    if run is not None and run.duration_ms is not None:
        return run.duration_ms
    metadata = source.source_metadata if source is not None and isinstance(source.source_metadata, dict) else {}
    for key in ("video_duration_ms", "duration_ms"):
        try:
            value = int(metadata.get(key))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _video_job_matches_filters(
    job: ProcessingJobModel,
    run: VideoAnalysisRunModel | None,
    *,
    now: datetime,
    from_at: datetime,
    to_at: datetime,
    provider: str | None,
    model: str | None,
    processing_mode: str | None,
    metadata_profile: str | None,
    status: str | None,
) -> bool:
    updated_at = _as_utc(job.updated_at)
    if updated_at is None or not (from_at <= updated_at < to_at):
        return False
    retry_at = _as_utc(job.next_attempt_at)
    if status == "waiting":
        if job.status not in _QUEUED or retry_at is None or retry_at <= now:
            return False
    elif status == "queued":
        if job.status not in _QUEUED or (retry_at is not None and retry_at > now):
            return False
    elif status == "running":
        if job.status not in _RUNNING:
            return False
    elif status and job.status != status:
        return False
    if provider and (run.ai_provider if run is not None else job.provider_key) != provider:
        return False
    if model and (run is None or run.ai_model != model):
        return False
    if processing_mode and processing_mode != "single":
        return False
    if metadata_profile and (run is None or run.metadata_profile != metadata_profile):
        return False
    return True


def _stage(key: str, label: str, rows: list[ProcessingJobModel], now: datetime) -> dict:
    counts = Counter(row.status for row in rows)
    deferred_quota_codes = {
        "ai_model_rate_limited", "rate_limited",
        "video_gemini_quota_deferred", "video_gemini_rate_limited",
    }
    deferred_by_quota = [
        row for row in rows
        if row.status in _QUEUED
        and row.last_error_code in deferred_quota_codes
        and (retry_at := _as_utc(row.next_attempt_at)) is not None
        and retry_at > now
    ]
    waiting_rate_limit = len(deferred_by_quota)
    next_quota_retry_at = min(
        (_as_utc(row.next_attempt_at) for row in deferred_by_quota),
        default=None,
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
        "deferred_by_quota": waiting_rate_limit,
        "next_quota_retry_at": next_quota_retry_at.isoformat() if next_quota_retry_at else None,
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

    def video_detail(self, tenant_id: str, source_asset_id: str) -> dict | None:
        """Return one tenant-scoped video item for the existing detail drawer."""
        source = self.session.scalar(select(SourceAssetModel).where(
            SourceAssetModel.tenant_id == tenant_id,
            SourceAssetModel.id == source_asset_id,
            SourceAssetModel.deleted_at.is_(None),
        ))
        if source is None:
            return None
        external = self.session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.id == source.external_source_id,
        ))
        if external is None:
            return None
        run = self.session.scalar(
            select(VideoAnalysisRunModel)
            .where(
                VideoAnalysisRunModel.tenant_id == tenant_id,
                VideoAnalysisRunModel.source_asset_id == source.id,
            )
            .order_by(
                VideoAnalysisRunModel.updated_at.desc(),
                VideoAnalysisRunModel.id.desc(),
            )
            .limit(1)
        )
        matches: list[dict] = []
        if run is not None:
            chunks = self.session.scalars(
                select(VideoAnalysisChunkModel)
                .where(
                    VideoAnalysisChunkModel.tenant_id == tenant_id,
                    VideoAnalysisChunkModel.run_id == run.id,
                    VideoAnalysisChunkModel.status == "completed",
                )
                .order_by(VideoAnalysisChunkModel.chunk_index)
            )
            for chunk in chunks:
                metadata = chunk.metadata_json if isinstance(chunk.metadata_json, dict) else {}
                segments = metadata.get("segments")
                if not isinstance(segments, list):
                    continue
                for segment in segments:
                    if not isinstance(segment, dict):
                        continue
                    start_ms, end_ms = segment.get("start_ms"), segment.get("end_ms")
                    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or start_ms < 0 or end_ms <= start_ms:
                        continue
                    confidence = segment.get("confidence")
                    matches.append({
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "summary": segment.get("summary") if isinstance(segment.get("summary"), str) else "",
                        "visual_description": segment.get("visual_description") if isinstance(segment.get("visual_description"), str) else "",
                        "speech": segment.get("speech") if isinstance(segment.get("speech"), str) else "",
                        "confidence": float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else 0.0,
                        "score": 0.0,
                    })
        matches.sort(key=lambda value: (value["start_ms"], value["end_ms"]))
        fallback = {
            "start_ms": 0,
            "end_ms": max(1, int(_video_duration_ms(source, run) or 1)),
            "summary": (run.summary_json or {}).get("summary", "") if run is not None else "",
            "visual_description": "",
            "speech": "",
            "confidence": 0.0,
            "score": 0.0,
        }
        metadata = source.source_metadata if isinstance(source.source_metadata, dict) else {}
        thumbnail_url = None
        if external.source_type == "google_drive":
            thumbnail_url = f"/api/explorer/thumbnail/{source.external_asset_id}?provider=google-drive&external_source_id={source.external_source_id}&fallback=video"
        analysis_job = self.session.scalar(
            select(ProcessingJobModel)
            .where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == VIDEO_JOB_TYPE,
                ProcessingJobModel.entity_type == "source_asset",
                ProcessingJobModel.entity_id == source.id,
            )
            .order_by(ProcessingJobModel.updated_at.desc(), ProcessingJobModel.id.desc())
            .limit(1)
        )
        index_job = None
        if run is not None:
            index_job = self.session.scalar(
                select(ProcessingJobModel)
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == VIDEO_INDEX_JOB_TYPE,
                    ProcessingJobModel.entity_type == "video_analysis_run",
                    ProcessingJobModel.entity_id == run.id,
                )
                .order_by(ProcessingJobModel.updated_at.desc(), ProcessingJobModel.id.desc())
                .limit(1)
            )

        def video_step(key: str, label: str, job: ProcessingJobModel | None) -> dict:
            updated_at = _as_utc(job.updated_at) if job is not None else None
            return {
                "key": key,
                "label": label,
                "status": job.status if job is not None else "not_started",
                "attempt_count": job.attempt_count if job is not None else 0,
                "max_attempts": job.max_attempts if job is not None else 0,
                "updated_at": updated_at.isoformat() if updated_at is not None else None,
                "error_code": job.last_error_code if job is not None else None,
            }

        return {
            "source_asset_id": source.id,
            "analysis_run_id": run.id if run is not None else "",
            "filename": source.filename or "Video",
            "mime_type": source.mime_type or "video/mp4",
            "duration_ms": _video_duration_ms(source, run),
            "source_type": external.source_type,
            "external_source_id": source.external_source_id,
            "external_asset_id": source.external_asset_id,
            "web_url": metadata.get("web_url") or metadata.get("webViewLink"),
            "thumbnail_url": thumbnail_url or metadata.get("thumbnail_url"),
            "location": _video_locations(self.session, tenant_id, [source], {external.id: external}).get(source.id),
            "size_bytes": source.size_bytes,
            "modified_at": source.source_modified_at.isoformat() if source.source_modified_at else None,
            "score": 0.0,
            "best_match": matches[0] if matches else fallback,
            "matches": matches,
            "steps": [
                video_step(VIDEO_JOB_TYPE, "Video analysis", analysis_job),
                video_step(VIDEO_INDEX_JOB_TYPE, "Video indexing", index_job),
            ],
        }

    async def snapshot(
        self,
        tenant_id: str,
        *,
        video_page: int = 1,
        video_page_size: int = 25,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        provider: str | None = None,
        model: str | None = None,
        processing_mode: str | None = None,
        metadata_profile: str | None = None,
        status: str | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        to_at = _as_utc(to_at) or now
        from_at = _as_utc(from_at) or datetime(1970, 1, 1, tzinfo=timezone.utc)
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
        video_jobs = by_type[VIDEO_JOB_TYPE]
        video_source_ids = {
            job.entity_id for job in video_jobs if job.entity_type == "source_asset"
        }
        latest_video_runs: dict[str, VideoAnalysisRunModel] = {}
        if video_source_ids:
            runs = self.session.scalars(
                select(VideoAnalysisRunModel)
                .where(
                    VideoAnalysisRunModel.tenant_id == tenant_id,
                    VideoAnalysisRunModel.source_asset_id.in_(video_source_ids),
                )
                .order_by(VideoAnalysisRunModel.updated_at.desc(), VideoAnalysisRunModel.id.desc())
            )
            for run in runs:
                latest_video_runs.setdefault(run.source_asset_id, run)
        filtered_video = [
            job for job in video_jobs
            if _video_job_matches_filters(
                job,
                latest_video_runs.get(job.entity_id),
                now=now,
                from_at=from_at,
                to_at=to_at,
                provider=provider,
                model=model,
                processing_mode=processing_mode,
                metadata_profile=metadata_profile,
                status=status,
            )
        ]
        ordered_video = sorted(filtered_video, key=lambda row: _as_utc(row.updated_at) or now, reverse=True)
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
        latest_runs = {
            source_id: latest_video_runs[source_id]
            for source_id in source_ids
            if source_id in latest_video_runs
        }
        latest_index_jobs: dict[str, ProcessingJobModel] = {}
        for index_job in sorted(
            by_type[VIDEO_INDEX_JOB_TYPE],
            key=lambda row: (
                _as_utc(row.updated_at) or datetime(1970, 1, 1, tzinfo=timezone.utc),
                row.id,
            ),
            reverse=True,
        ):
            if index_job.entity_type == "video_analysis_run":
                latest_index_jobs.setdefault(index_job.entity_id, index_job)
        logical_asset_ids: dict[str, str] = {}
        if source_ids:
            links = self.session.execute(
                select(AssetSourceLinkModel.source_asset_id, AssetSourceLinkModel.asset_id).where(
                    AssetSourceLinkModel.tenant_id == tenant_id,
                    AssetSourceLinkModel.source_asset_id.in_(source_ids),
                )
            )
            for source_asset_id, asset_id in links:
                logical_asset_ids.setdefault(source_asset_id, asset_id)
        analytics_runs = list(self.session.scalars(select(VideoAnalysisRunModel).where(
            VideoAnalysisRunModel.tenant_id == tenant_id,
        )))
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_analytics = _video_analytics(
            analytics_runs,
            from_at=today_start,
            to_at=now,
            provider=provider,
            model=model,
            processing_mode=processing_mode,
            metadata_profile=metadata_profile,
            status=status,
        )
        video_processed_today = sum(
            int(day["completed"])
            for day in today_analytics["daily"]
        )
        recent_video = []
        for job in page_jobs:
            source = self.session.get(SourceAssetModel, job.entity_id) if job.entity_type == "source_asset" else None
            external = external_sources.get(source.external_source_id) if source is not None else None
            run = latest_runs.get(source.id) if source is not None else None
            index_job = latest_index_jobs.get(run.id) if run is not None else None
            thumbnail_url = None
            if source is not None and external is not None and external.source_type == "google_drive":
                thumbnail_url = f"/api/explorer/thumbnail/{source.external_asset_id}?provider=google-drive&external_source_id={source.external_source_id}&fallback=video"
            recent_video.append({
                "job_id": job.id, "source_asset_id": job.entity_id,
                "asset_id": logical_asset_ids.get(job.entity_id),
                "filename": source.filename if source is not None else None,
                "mime_type": source.mime_type if source is not None else None,
                "location": locations.get(source.id) if source is not None else None,
                "thumbnail_url": thumbnail_url,
                "duration_ms": _video_duration_ms(source, run),
                "completed_chunks": run.completed_chunks if run is not None else 0,
                "total_chunks": run.total_chunks if run is not None else 0,
                "status": job.status, "attempt_count": job.attempt_count,
                "max_attempts": job.max_attempts,
                "updated_at": (_as_utc(job.updated_at) or now).isoformat(),
                "error_code": job.last_error_code,
                "error_message": redact_url_queries(job.last_error_message),
                "steps": [
                    {
                        "key": VIDEO_JOB_TYPE,
                        "label": "Video analysis",
                        "status": job.status,
                        "attempt_count": job.attempt_count,
                        "max_attempts": job.max_attempts,
                        "updated_at": (_as_utc(job.updated_at) or now).isoformat(),
                        "error_code": job.last_error_code,
                        "error_message": redact_url_queries(job.last_error_message),
                    },
                    {
                        "key": VIDEO_INDEX_JOB_TYPE,
                        "label": "Video indexing",
                        "status": index_job.status if index_job is not None else "not_started",
                        "attempt_count": index_job.attempt_count if index_job is not None else 0,
                        "max_attempts": index_job.max_attempts if index_job is not None else 0,
                        "updated_at": (
                            (_as_utc(index_job.updated_at) or now).isoformat()
                            if index_job is not None else None
                        ),
                        "error_code": index_job.last_error_code if index_job is not None else None,
                        "error_message": redact_url_queries(index_job.last_error_message) if index_job is not None else None,
                    },
                ],
            })
        return {
            "image": image,
            "video": video,
            "video_indexing": indexing,
            "video_processed_today": video_processed_today,
            "pipeline": {"image": [image], "video": [video, indexing]},
            "recent_video": {"page": video_page, "page_size": video_page_size, "total": len(ordered_video), "items": recent_video},
            "workers": [
                worker("image", (IMAGE_JOB_TYPE,), image_probe),
                worker("video", (VIDEO_JOB_TYPE, VIDEO_INDEX_JOB_TYPE), video_probe),
            ],
            "analytics": _video_analytics(
                analytics_runs,
                from_at=from_at,
                to_at=to_at,
                provider=provider,
                model=model,
                processing_mode=processing_mode,
                metadata_profile=metadata_profile,
                status=status,
            ),
            "generated_at": now.isoformat(),
        }
