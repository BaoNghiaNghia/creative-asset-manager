from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.drive.metrics import inventory_drive_metrics
from app.modules.inventory.drive.storage import (
    InventorySourceStorage,
    InventoryStorageError,
)
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import (
    InventorySourceFileModel,
    inventory_utcnow,
)
from app.providers.google.auth import get_connection_access_token
from app.providers.google.drive import (
    GoogleDriveClient,
    close_media_stream,
    open_media_stream,
)


logger = logging.getLogger("cam.inventory.drive")


class InventoryDownloadFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _same_instant(left: datetime | None, right: datetime) -> bool:
    if left is None:
        return False
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)


class InventoryFileDownloader:
    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        storage: InventorySourceStorage,
        max_bytes: int,
        token_resolver: Callable = get_connection_access_token,
        client_factory: Callable = GoogleDriveClient,
        stream_opener: Callable = open_media_stream,
        stream_closer: Callable = close_media_stream,
    ):
        self.session_factory = session_factory
        self.storage = storage
        self.max_bytes = max_bytes
        self.token_resolver = token_resolver
        self.client_factory = client_factory
        self.stream_opener = stream_opener
        self.stream_closer = stream_closer

    async def execute(self, job: InventoryJobModel) -> None:
        source_file_id = str((job.payload_json or {}).get("source_file_id") or job.entity_id)
        source = self._begin_download(job.tenant_id, source_file_id)
        if source is None:
            return
        try:
            connection_id = self._connection_id(
                source.tenant_id, source.external_source_id
            )
            access_token = await self.token_resolver(connection_id)
            async with self.client_factory(access_token) as drive:
                provider_item = await drive.get(source.drive_file_id)
            if not _same_instant(provider_item.modified_at, source.drive_modified_time):
                raise InventoryDownloadFailure(
                    "provider_version_changed", retryable=False
                )

            client = response = None
            pending = None
            try:
                client, response = await self.stream_opener(
                    access_token, source.drive_file_id, None
                )
                pending = await self.storage.prepare(
                    tenant_id=source.tenant_id,
                    source_file_id=source.id,
                    body=response.aiter_bytes(),
                    max_bytes=self.max_bytes,
                )
            finally:
                if client is not None and response is not None:
                    await self.stream_closer(client, response)
            if pending is None:
                raise InventoryDownloadFailure(
                    "empty_provider_download", retryable=True
                )
            try:
                self._finish_download(source, pending)
            except Exception:
                pending.discard()
                raise
        except InventoryDownloadFailure as exc:
            self._record_failure(source.tenant_id, source.id, exc)
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status == 429 or status >= 500
            failure = InventoryDownloadFailure(
                f"google_drive_http_{status}", retryable=retryable
            )
            self._record_failure(source.tenant_id, source.id, failure)
            raise failure from exc
        except (httpx.TimeoutException, httpx.NetworkError, OSError) as exc:
            failure = InventoryDownloadFailure(
                "inventory_download_transport_failure", retryable=True
            )
            self._record_failure(source.tenant_id, source.id, failure)
            raise failure from exc
        except InventoryStorageError as exc:
            retryable = str(exc) != "inventory_source_too_large"
            failure = InventoryDownloadFailure(str(exc), retryable=retryable)
            self._record_failure(source.tenant_id, source.id, failure)
            raise failure from exc
        except Exception as exc:
            failure = InventoryDownloadFailure(
                "inventory_download_unexpected", retryable=True
            )
            self._record_failure(source.tenant_id, source.id, failure)
            raise failure from exc

    def _begin_download(
        self, tenant_id: str, source_file_id: str
    ) -> InventorySourceFileModel | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(InventorySourceFileModel).where(
                    InventorySourceFileModel.tenant_id == tenant_id,
                    InventorySourceFileModel.id == source_file_id,
                )
            )
            if row is None:
                raise InventoryDownloadFailure(
                    "inventory_source_file_not_found", retryable=False
                )
            if row.status in {"downloaded", "duplicate"}:
                return None
            if row.status in {"unsupported", "terminal_failure"}:
                raise InventoryDownloadFailure(
                    "inventory_source_file_terminal", retryable=False
                )
            row.status = "downloading"
            row.last_error_code = None
            row.last_error_message = None
            session.commit()
            session.expunge(row)
            return row

    def _connection_id(self, tenant_id: str, external_source_id: str) -> str:
        with self.session_factory() as session:
            source = session.scalar(
                select(ExternalSourceModel).where(
                    ExternalSourceModel.tenant_id == tenant_id,
                    ExternalSourceModel.id == external_source_id,
                    ExternalSourceModel.source_type == "google_drive",
                )
            )
            connection_id = (
                (source.source_metadata or {}).get("oauth_connection_id")
                if source is not None
                else None
            )
            if not isinstance(connection_id, str) or not connection_id:
                raise InventoryDownloadFailure(
                    "inventory_google_connection_unavailable", retryable=True
                )
            return connection_id

    def _finish_download(self, source: InventorySourceFileModel, pending) -> None:
        with self.session_factory() as session:
            row = session.scalar(
                select(InventorySourceFileModel).where(
                    InventorySourceFileModel.tenant_id == source.tenant_id,
                    InventorySourceFileModel.id == source.id,
                )
            )
            if row is None:
                pending.discard()
                raise InventoryDownloadFailure(
                    "inventory_source_file_not_found", retryable=False
                )
            if row.status in {"downloaded", "duplicate"}:
                pending.discard()
                return
            duplicate = session.scalar(
                select(InventorySourceFileModel)
                .where(
                    InventorySourceFileModel.tenant_id == source.tenant_id,
                    InventorySourceFileModel.id != source.id,
                    InventorySourceFileModel.content_sha256 == pending.sha256,
                    InventorySourceFileModel.storage_key.is_not(None),
                    InventorySourceFileModel.status.in_(("downloaded", "duplicate")),
                )
                .order_by(InventorySourceFileModel.downloaded_at, InventorySourceFileModel.id)
                .limit(1)
            )
            row.content_sha256 = pending.sha256
            row.drive_size = pending.size_bytes
            row.downloaded_at = inventory_utcnow()
            if duplicate is not None:
                pending.discard()
                row.status = "duplicate"
                row.duplicate_of_source_file_id = (
                    duplicate.duplicate_of_source_file_id or duplicate.id
                )
                row.storage_key = duplicate.storage_key
                inventory_drive_metrics.increment("duplicate_content")
            else:
                suffix = PurePath(row.filename).suffix
                row.storage_key = pending.commit(suffix)
                row.status = "downloaded"
                row.duplicate_of_source_file_id = None
            row.last_error_code = None
            row.last_error_message = None
            session.commit()
            inventory_drive_metrics.increment("download_succeeded")
            inventory_drive_metrics.increment("download_bytes", pending.size_bytes)
            logger.info(
                "inventory_download_completed tenant_id=%s external_source_id=%s source_file_id=%s bytes_downloaded=%s duplicate=%s",
                row.tenant_id,
                row.external_source_id,
                row.id,
                pending.size_bytes,
                duplicate is not None,
            )

    def _record_failure(
        self,
        tenant_id: str,
        source_file_id: str,
        failure: InventoryDownloadFailure,
    ) -> None:
        with self.session_factory() as session:
            row = session.scalar(
                select(InventorySourceFileModel).where(
                    InventorySourceFileModel.tenant_id == tenant_id,
                    InventorySourceFileModel.id == source_file_id,
                )
            )
            if row is None or row.status in {"downloaded", "duplicate"}:
                return
            row.status = (
                "retryable_failure" if failure.retryable else "terminal_failure"
            )
            row.last_error_code = failure.code[:100]
            row.last_error_message = failure.code[:1000]
            row.updated_at = inventory_utcnow()
            session.commit()
        inventory_drive_metrics.increment(
            "download_retryable_failure"
            if failure.retryable
            else "download_terminal_failure"
        )
        logger.warning(
            "inventory_download_failed tenant_id=%s source_file_id=%s error_code=%s retryable=%s",
            tenant_id,
            source_file_id,
            failure.code,
            failure.retryable,
        )
