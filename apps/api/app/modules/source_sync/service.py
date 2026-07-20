from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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


class SourceSyncService:
    def __init__(
        self,
        repository: SourceSyncRepository,
        processing: ProcessingRepository,
        *,
        enabled: bool = False,
    ):
        if repository.session is not processing.session:
            raise ValueError("source sync and processing repositories must share one transaction")
        self.repository = repository
        self.processing = processing
        self.enabled = enabled

    async def sync_source(
        self,
        *,
        tenant_id: str,
        source_id: str,
        provider: AssetSourceProvider,
        page_size: int = 100,
        max_pages: int = 1000,
        reconciliation: bool = False,
    ) -> SourceSyncResult:
        if not self.enabled:
            raise RuntimeError("incremental source sync is disabled")
        source = self.repository.get_source(tenant_id, source_id)
        if source is None:
            raise LookupError(source_id)
        cursor_key = "reconciliation" if reconciliation else "changes"
        cursor = None if reconciliation else self.repository.get_cursor(
            tenant_id, source_id, cursor_key
        )
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        pages = changes_count = jobs_created = 0
        seen: set[str] = set()
        while pages < max_pages:
            page = await provider.list_changes(
                ListSourceChangesInput(
                    source_id=source_id,
                    cursor=cursor,
                    page_size=page_size,
                    source_metadata=dict(source.source_metadata or {}),
                    reconciliation=reconciliation,
                )
            )
            try:
                for change in page.changes:
                    changes_count += 1
                    if reconciliation:
                        seen.add(change.external_asset_id)
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
                    old_marker = None if existing is None else (
                        existing.provider_checksum or existing.provider_version
                    )
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
                        source_metadata=candidate.source_metadata,
                    )
                    is_folder = bool(candidate.source_metadata.get("is_folder"))
                    content_maybe_changed = existing is None or (
                        new_marker is not None and old_marker != new_marker
                    )
                    if not is_folder and content_maybe_changed and not (
                        was_deleted and old_marker == new_marker
                    ):
                        before = self.processing.count_jobs()
                        self.processing.create_job(
                            tenant_id=tenant_id,
                            job_type="source_asset_download",
                            entity_type="source_asset",
                            entity_id=source_asset.id,
                            idempotency_key=(
                                f"source-asset-download:{source_asset.id}:"
                                f"{new_marker or candidate.source_modified_at or 'unknown'}"
                            ),
                            payload={"source_asset_id": source_asset.id},
                            provider_key=source.source_type,
                            provider_scope="source",
                        )
                        jobs_created += int(self.processing.count_jobs() > before)
                if page.next_cursor is not None:
                    self.repository.assets.save_sync_cursor(
                        tenant_id=tenant_id,
                        external_source_id=source_id,
                        cursor_key=cursor_key,
                        cursor_value=page.next_cursor,
                    )
                self.repository.session.commit()
            except Exception:
                self.repository.session.rollback()
                raise
            pages += 1
            cursor = page.next_cursor
            if not page.has_more:
                break
        if pages >= max_pages and page.has_more:
            raise RuntimeError("source sync page limit exceeded")
        if reconciliation:
            missing = self.repository.list_external_ids(tenant_id, source_id) - seen
            try:
                for external_id in missing:
                    source_asset = self.repository.get_source_asset_by_external_id(
                        tenant_id, source_id, external_id
                    )
                    if source_asset is not None:
                        self.repository.assets.mark_source_asset_deleted(
                            tenant_id=tenant_id, source_asset_id=source_asset.id
                        )
                self.repository.session.commit()
            except Exception:
                self.repository.session.rollback()
                raise
        return SourceSyncResult(pages, changes_count, jobs_created, cursor, reconciliation)
