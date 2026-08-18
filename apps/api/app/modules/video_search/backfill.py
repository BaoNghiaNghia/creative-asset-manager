from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select

from app.core.config import Settings
from app.modules.assets.model import SourceAssetModel
from app.modules.pipeline.mime_types import is_eligible_video_source_asset
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_search.enqueue import enqueue_video_analysis_job
from app.modules.video_search.fingerprint import build_video_source_fingerprint
from app.modules.video_search.repository import VideoSearchRepository


@dataclass
class VideoBackfillResult:
    scanned: int = 0
    eligible: int = 0
    enqueued: int = 0
    skipped_completed: int = 0
    skipped_existing_job: int = 0
    skipped_no_profile: int = 0
    skipped_unsupported: int = 0
    errors: int = 0


class VideoAnalysisBackfillService:
    def __init__(self, processing: ProcessingRepository, *, settings: Settings):
        self.processing = processing
        self.settings = settings
        self.session = processing.session

    def run(self, *, tenant_id: str, source_asset_id: str | None = None, limit: int = 100, dry_run: bool = False) -> VideoBackfillResult:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if limit < 1:
            raise ValueError("limit must be positive")
        result = VideoBackfillResult()
        profiles = VideoSearchRepository(self.session)
        profile = profiles.get_active_profile(tenant_id)
        if profile is None:
            result.skipped_no_profile = 1
            return result
        statement = select(SourceAssetModel).where(
            SourceAssetModel.tenant_id == tenant_id,
            SourceAssetModel.deleted_at.is_(None),
        )
        if source_asset_id:
            statement = statement.where(SourceAssetModel.id == source_asset_id)
        assets = self.session.scalars(statement.order_by(SourceAssetModel.id.asc()).limit(limit)).all()
        for asset in assets:
            result.scanned += 1
            if not is_eligible_video_source_asset(asset):
                result.skipped_unsupported += 1
                continue
            result.eligible += 1
            identity = {
                "tenant_id": tenant_id, "source_asset_id": asset.id,
                "source_fingerprint": build_video_source_fingerprint(asset),
                "video_metadata_profile_id": profile.id, "metadata_profile": profile.profile_name,
                "metadata_profile_version": profile.profile_version,
                "prompt_version": self.settings.VIDEO_AI_PROMPT_VERSION,
                "analysis_version": self.settings.VIDEO_AI_ANALYSIS_VERSION,
                "ai_provider": "gemini",
            }
            if profiles.find_completed_compatible_run(**identity) is not None:
                result.skipped_completed += 1
                continue
            if profiles.find_resumable_compatible_run(**identity) is not None:
                result.skipped_existing_job += 1
                continue
            if dry_run:
                result.enqueued += 1
                continue
            try:
                if enqueue_video_analysis_job(tenant_id=tenant_id, source_asset=asset, processing=self.processing, settings=self.settings):
                    result.enqueued += 1
                else:
                    result.skipped_existing_job += 1
            except Exception:
                result.errors += 1
        return result
