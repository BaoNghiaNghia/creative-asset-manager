from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.core.redaction import sanitize_sensitive_urls
from app.domain.providers.contracts import AssetSourceProvider, ListSourceChangesInput
from app.modules.authorization.folder_scope_cache import (
    viewer_folder_hierarchy_cache,
    viewer_folder_remote_parent_cache,
)
from app.modules.pipeline.mime_types import is_supported_google_drive_image_mime_type, is_eligible_video_source_asset
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.video_search.enqueue import enqueue_video_analysis_job
from app.modules.video_search.fingerprint import build_video_source_fingerprint
from app.core.config import Settings
from app.modules.processing.repository import ProcessingRepository
from app.modules.source_sync.repository import SourceSyncRepository
from app.modules.explorer.breadcrumb import location_breadcrumb_cache
from app.modules.explorer.cache import (
    invalidate_drive_listings,
    invalidate_thumbnail,
)
from app.modules.explorer.preview import preview_cache_invalidate
from app.providers.google.internal_files import is_cam_managed_file


def _invalidate_viewer_folder_hierarchy(*, tenant_id: str, external_source_id: str) -> None:
    """Drop a source hierarchy snapshot after a committed source mutation."""
    viewer_folder_hierarchy_cache.invalidate(
        tenant_id=tenant_id, external_source_id=external_source_id
    )
    viewer_folder_remote_parent_cache.invalidate(
        tenant_id=tenant_id, external_source_id=external_source_id
    )
    location_breadcrumb_cache.invalidate(tenant_id=tenant_id, external_source_id=external_source_id)
    invalidate_drive_listings(
        tenant_id=tenant_id, external_source_id=external_source_id
    )
    invalidate_thumbnail(
        tenant_id=tenant_id, external_source_id=external_source_id
    )
    preview_cache_invalidate(
        tenant_id=tenant_id, external_source_id=external_source_id
    )


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def initial_source_asset_download_key(source_asset_id: str) -> str:
    return f"source-asset-download:{source_asset_id}:initial-import-v2"


@dataclass(frozen=True)
class SourceSyncResult:
    pages: int
    changes: int
    jobs_created: int
    cursor: str | None
    reconciliation: bool = False
    run_id: str | None = None
    generation: int | None = None
    missing_marked: int = 0


class SourceSyncService:
    def __init__(self, repository: SourceSyncRepository, processing: ProcessingRepository, *, enabled: bool = False, settings: Settings | None = None):
        if repository.session is not processing.session:
            raise ValueError("source sync and processing repositories must share one transaction")
        self.repository = repository
        self.processing = processing
        self.enabled = enabled
        self.settings = settings

    async def sync_source(
        self, *, tenant_id: str, source_id: str, provider: AssetSourceProvider,
        page_size: int = 100, max_pages: int = 1000, reconciliation: bool = False,
        continue_check: Callable[[], bool] | None = None,
    ) -> SourceSyncResult:
        if not self.enabled:
            raise RuntimeError("incremental source sync is disabled")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        source = self.repository.get_source(tenant_id, source_id)
        if source is None:
            raise LookupError(source_id)

        run = None
        if reconciliation:
            run = self.repository.start_or_resume_full_run(tenant_id, source_id)
            cursor = run.checkpoint_cursor
            self.repository.session.commit()
        else:
            cursor = self.repository.get_cursor(tenant_id, source_id, "changes")
        pages = changes_count = jobs_created = 0
        has_more = True
        pipelines = AssetPipelineRepository(self.repository.session)

        while pages < max_pages and has_more:
            if continue_check is not None and not continue_check():
                if run is not None:
                    self.repository.cancel_run(run.id, "lease_or_cancellation")
                    self.repository.session.commit()
                raise RuntimeError("source sync interrupted before page fetch")
            try:
                page = await provider.list_changes(ListSourceChangesInput(
                    source_id=source_id,
                    cursor=cursor,
                    page_size=page_size,
                    source_metadata=dict(source.source_metadata or {}),
                    reconciliation=reconciliation,
                ))
                page_changes = page_jobs = 0
                active_generation = run.generation if run else self.repository.active_full_generation(tenant_id, source_id)
                for change in page.changes:
                    changes_count += 1
                    page_changes += 1
                    if change.change_type == "deleted":
                        existing = self.repository.get_source_asset_by_external_id(
                            tenant_id, source_id, change.external_asset_id
                        )
                        if existing is not None and existing.deleted_at is None:
                            self.repository.assets.mark_source_asset_deleted(
                                tenant_id=tenant_id, source_asset_id=existing.id
                            )
                        continue
                    candidate = change.candidate
                    if candidate is None:
                        raise ValueError("non-delete change requires a candidate")
                    # Defense in depth for Google providers that produce candidates
                    # directly instead of going through incremental.py.
                    if source.source_type == "google_drive" and is_cam_managed_file(candidate.source_metadata):
                        existing = self.repository.get_source_asset_by_external_id(
                            tenant_id, source_id, candidate.external_asset_id
                        )
                        if existing is not None and existing.deleted_at is None:
                            self.repository.assets.mark_source_asset_deleted(
                                tenant_id=tenant_id, source_asset_id=existing.id
                            )
                        continue
                    existing = self.repository.get_source_asset_by_external_id(
                        tenant_id, source_id, candidate.external_asset_id
                    )
                    was_deleted = existing is not None and existing.deleted_at is not None
                    old_marker = None if existing is None else (existing.provider_checksum or existing.provider_version)
                    old_video_fingerprint = None if existing is None else build_video_source_fingerprint(existing)
                    new_marker = candidate.provider_checksum or candidate.provider_version
                    candidate_metadata = dict(candidate.source_metadata or {})
                    if not candidate_metadata.get("web_url") and existing is not None:
                        previous_metadata = dict(existing.source_metadata or {})
                        for key in ("web_url", "webViewLink", "webUrl", "source_web_url"):
                            if previous_metadata.get(key):
                                candidate_metadata["web_url"] = previous_metadata[key]
                                break
                    source_asset = self.repository.assets.upsert_source_asset(
                        tenant_id=tenant_id,
                        external_source_id=source_id,
                        external_asset_id=candidate.external_asset_id,
                        filename=candidate.filename,
                        mime_type=candidate.mime_type,
                        size_bytes=candidate.size_bytes,
                        source_created_at=_datetime(candidate.source_created_at),
                        source_modified_at=_datetime(candidate.source_modified_at),
                        provider_checksum=candidate.provider_checksum,
                        provider_version=candidate.provider_version,
                        source_metadata=sanitize_sensitive_urls(candidate_metadata),
                    )
                    # Incremental discoveries during a full traversal join its generation,
                    # preventing a later successful sweep from deleting the new item.
                    if active_generation is not None:
                        self.repository.mark_seen(source_asset, active_generation)
                    is_folder = bool(candidate.source_metadata.get("is_folder"))
                    is_video = not is_folder and is_eligible_video_source_asset(source_asset)
                    is_download_supported = (
                        source.source_type != "google_drive"
                        or is_supported_google_drive_image_mime_type(candidate.mime_type)
                    )
                    content_maybe_changed = existing is None or (new_marker is not None and old_marker != new_marker)
                    never_imported = pipelines.get_by_origin(
                        tenant_id, "source_asset", source_asset.id
                    ) is None
                    video_content_changed = existing is None or old_video_fingerprint != build_video_source_fingerprint(source_asset)
                    if is_video and video_content_changed:
                        created = int(enqueue_video_analysis_job(
                            tenant_id=tenant_id, source_asset=source_asset,
                            processing=self.processing, settings=self.settings,
                        ))
                        jobs_created += created
                        page_jobs += created
                    if (
                        not is_folder
                        and not is_video
                        and is_download_supported
                        and (content_maybe_changed or never_imported)
                        and not (was_deleted and old_marker == new_marker)
                    ):
                        before = self.processing.count_jobs()
                        initial_key = initial_source_asset_download_key(source_asset.id)
                        initial_job_exists = self.processing.get_job_by_key(
                            tenant_id, initial_key
                        ) is not None
                        initial_import = never_imported and (
                            existing is None
                            or not content_maybe_changed
                            or not initial_job_exists
                        )
                        idempotency_key = (
                            initial_key
                            if initial_import
                            else f"source-asset-download:{source_asset.id}:{new_marker or candidate.source_modified_at or 'unknown'}"
                        )
                        self.processing.create_job(
                            tenant_id=tenant_id,
                            job_type="source_asset_download",
                            entity_type="source_asset",
                            entity_id=source_asset.id,
                            idempotency_key=idempotency_key,
                            payload={"source_asset_id": source_asset.id},
                            provider_key=source.source_type,
                            provider_scope="source",
                        )
                        created = int(self.processing.count_jobs() > before)
                        jobs_created += created
                        page_jobs += created
                if reconciliation and run is not None:
                    self.repository.checkpoint_run(run, cursor=page.next_cursor, items=page_changes, jobs=page_jobs)
                elif page.next_cursor is not None:
                    self.repository.assets.save_sync_cursor(
                        tenant_id=tenant_id, external_source_id=source_id,
                        cursor_key="changes", cursor_value=page.next_cursor,
                    )
                self.repository.session.commit()
                if page_changes:
                    _invalidate_viewer_folder_hierarchy(
                        tenant_id=tenant_id, external_source_id=source_id,
                    )
            except Exception as exc:
                self.repository.session.rollback()
                if run is not None:
                    self.repository.fail_run(run.id, type(exc).__name__)
                    self.repository.session.commit()
                raise
            pages += 1
            cursor = page.next_cursor
            has_more = page.has_more

        if has_more:
            if run is not None:
                self.repository.fail_run(run.id, "PageLimitExceeded")
                self.repository.session.commit()
            raise RuntimeError("source sync page limit exceeded")

        missing_marked = 0
        if run is not None:
            if continue_check is not None and not continue_check():
                self.repository.cancel_run(run.id, "lease_or_cancellation")
                self.repository.session.commit()
                raise RuntimeError("source sync interrupted before reconciliation sweep")
            try:
                missing_marked = self.repository.complete_full_run(run)
                self.repository.session.commit()
                if missing_marked:
                    _invalidate_viewer_folder_hierarchy(
                        tenant_id=tenant_id,
                        external_source_id=source.id,
                    )
            except Exception as exc:
                self.repository.session.rollback()
                self.repository.fail_run(run.id, type(exc).__name__)
                self.repository.session.commit()
                raise
        return SourceSyncResult(
            pages, changes_count, jobs_created, cursor, reconciliation,
            run.id if run else None, run.generation if run else None, missing_marked,
        )
