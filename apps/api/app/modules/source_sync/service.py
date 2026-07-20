from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.core.redaction import sanitize_sensitive_urls
from app.domain.providers.contracts import AssetSourceProvider, ListSourceChangesInput
from app.modules.processing.repository import ProcessingRepository
from app.modules.source_sync.repository import SourceSyncRepository


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


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
    def __init__(self, repository: SourceSyncRepository, processing: ProcessingRepository, *, enabled: bool = False):
        if repository.session is not processing.session:
            raise ValueError("source sync and processing repositories must share one transaction")
        self.repository = repository
        self.processing = processing
        self.enabled = enabled

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
                    existing = self.repository.get_source_asset_by_external_id(
                        tenant_id, source_id, candidate.external_asset_id
                    )
                    was_deleted = existing is not None and existing.deleted_at is not None
                    old_marker = None if existing is None else (existing.provider_checksum or existing.provider_version)
                    new_marker = candidate.provider_checksum or candidate.provider_version
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
                        source_metadata=sanitize_sensitive_urls(candidate.source_metadata),
                    )
                    # Incremental discoveries during a full traversal join its generation,
                    # preventing a later successful sweep from deleting the new item.
                    if active_generation is not None:
                        self.repository.mark_seen(source_asset, active_generation)
                    is_folder = bool(candidate.source_metadata.get("is_folder"))
                    content_maybe_changed = existing is None or (new_marker is not None and old_marker != new_marker)
                    if not is_folder and content_maybe_changed and not (was_deleted and old_marker == new_marker):
                        before = self.processing.count_jobs()
                        self.processing.create_job(
                            tenant_id=tenant_id,
                            job_type="source_asset_download",
                            entity_type="source_asset",
                            entity_id=source_asset.id,
                            idempotency_key=f"source-asset-download:{source_asset.id}:{new_marker or candidate.source_modified_at or 'unknown'}",
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
            except Exception as exc:
                self.repository.session.rollback()
                self.repository.fail_run(run.id, type(exc).__name__)
                self.repository.session.commit()
                raise
        return SourceSyncResult(
            pages, changes_count, jobs_created, cursor, reconciliation,
            run.id if run else None, run.generation if run else None, missing_marked,
        )
