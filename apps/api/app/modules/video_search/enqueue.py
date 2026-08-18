from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.config import Settings, get_settings
from app.modules.pipeline.mime_types import is_eligible_video_source_asset
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_search.fingerprint import build_video_source_fingerprint
from app.modules.video_search.repository import VideoSearchRepository

_VIDEO_JOB_FLAGS = (
    "PROCESSING_JOBS_ENABLED",
    "VIDEO_SEARCH_ENABLED",
    "VIDEO_ANALYSIS_ENABLED",
    "VIDEO_PROXY_ENABLED",
)


def _key(identity: dict[str, Any]) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return "video-analyze:" + hashlib.sha256(encoded).hexdigest()


def enqueue_video_analysis_job(
    *,
    tenant_id: str,
    source_asset: Any,
    processing: ProcessingRepository,
    settings: Settings | None = None,
) -> bool:
    """Persist a deduplicated metadata-only video analysis job when enabled."""
    if not tenant_id or source_asset.tenant_id != tenant_id:
        raise ValueError("tenant-scoped video enqueue requires its source asset")
    if source_asset.deleted_at is not None or not is_eligible_video_source_asset(source_asset):
        return False
    settings = settings or get_settings()
    if not all(getattr(settings, flag) for flag in _VIDEO_JOB_FLAGS):
        return False
    profile = VideoSearchRepository(processing.session).get_active_profile(tenant_id)
    if profile is None:
        return False
    identity = {
        "tenant_id": tenant_id,
        "source_asset_id": source_asset.id,
        "source_fingerprint": build_video_source_fingerprint(source_asset),
        "video_metadata_profile_id": profile.id,
        "metadata_profile": profile.profile_name,
        "metadata_profile_version": profile.profile_version,
        "prompt_version": settings.VIDEO_AI_PROMPT_VERSION,
        "analysis_version": settings.VIDEO_AI_ANALYSIS_VERSION,
        "ai_provider": "gemini",
    }
    before = processing.count_jobs()
    processing.create_job(
        tenant_id=tenant_id,
        job_type="video_analyze",
        entity_type="source_asset",
        entity_id=source_asset.id,
        idempotency_key=_key(identity),
        payload=identity,
        provider_key="gemini",
        provider_scope="video",
    )
    return processing.count_jobs() > before
