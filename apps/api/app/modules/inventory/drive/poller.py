from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import SessionLocal
from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.drive.metrics import inventory_drive_metrics
from app.modules.inventory.drive.mime import (
    is_google_drive_folder,
    is_supported_inventory_image,
    normalize_inventory_mime_type,
)
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.model import InventoryProcessingControlModel
from app.modules.inventory.persistence_model import InventorySettingsModel
from app.modules.inventory.repository import InventorySourceFileRepository
from app.modules.inventory.schema import InventorySourceFileInput
from app.providers.google.auth import get_connection_access_token
from app.providers.google.drive import GoogleDriveClient


INVENTORY_FILE_DOWNLOAD_JOB = "inventory_file_download"
_REGISTERED_PHASE3_JOBS = (INVENTORY_FILE_DOWNLOAD_JOB,)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
logger = logging.getLogger("cam.inventory.drive")

def _safe_poll_error_code(exc: Exception) -> str:
    message = str(exc)
    if message.startswith("inventory_") and message.replace("_", "").isalnum():
        return message[:100]
    return type(exc).__name__[:100]



@dataclass(slots=True)
class InventoryPollSummary:
    bindings: int = 0
    files_listed: int = 0
    provider_versions_created: int = 0
    provider_versions_repeated: int = 0
    unsupported: int = 0
    folders_ignored: int = 0
    jobs_created: int = 0


class InventoryDrivePoller:
    def __init__(
        self,
        session: Session,
        *,
        automation_enabled: bool,
        poller_enabled: bool,
        token_resolver: Callable = get_connection_access_token,
        client_factory: Callable = GoogleDriveClient,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        allowed_tenant_ids: frozenset[str] | None = None,
    ):
        self.session = session
        self.automation_enabled = automation_enabled
        self.poller_enabled = poller_enabled
        self.token_resolver = token_resolver
        self.client_factory = client_factory
        self.clock = clock
        self.allowed_tenant_ids = allowed_tenant_ids

    async def poll_due(self) -> InventoryPollSummary:
        summary = InventoryPollSummary()
        if not (self.automation_enabled and self.poller_enabled):
            return summary
        now = self.clock()
        bindings = tuple(
            self.session.scalars(
                select(InventorySettingsModel)
                .join(
                    InventoryProcessingControlModel,
                    InventoryProcessingControlModel.tenant_id
                    == InventorySettingsModel.tenant_id,
                )
                .where(
                    InventorySettingsModel.enabled.is_(True),
                    InventoryProcessingControlModel.enabled.is_(True),
                    InventoryProcessingControlModel.paused.is_(False),
                )
                .order_by(InventorySettingsModel.tenant_id)
            )
        )
        for binding in bindings:
            if self.allowed_tenant_ids is not None and binding.tenant_id not in self.allowed_tenant_ids:
                continue
            last_poll = binding.last_successful_poll_at
            if last_poll is not None:
                if last_poll.tzinfo is None:
                    last_poll = last_poll.replace(tzinfo=timezone.utc)
                if last_poll + timedelta(seconds=binding.drive_poll_interval_seconds) > now:
                    continue
            summary.bindings += 1
            try:
                current = await self.poll_binding(binding)
                summary.files_listed += current.files_listed
                summary.provider_versions_created += current.provider_versions_created
                summary.provider_versions_repeated += current.provider_versions_repeated
                summary.unsupported += current.unsupported
                summary.folders_ignored += current.folders_ignored
                summary.jobs_created += current.jobs_created
                binding.last_successful_poll_at = now
                binding.last_poll_error_code = None
                binding.last_poll_error_message = None
                self.session.commit()
            except Exception as exc:
                self.session.rollback()
                error_code = _safe_poll_error_code(exc)
                failed = self.session.get(InventorySettingsModel, binding.id)
                if failed is not None:
                    failed.last_poll_error_code = error_code
                    failed.last_poll_error_message = (
                        "Inventory Drive polling failed; inspect structured server logs."
                    )
                    self.session.commit()
                logger.warning(
                    "inventory_drive_poll_failed tenant_id=%s external_source_id=%s error_code=%s",
                    binding.tenant_id,
                    binding.external_source_id,
                    error_code,
                )
        return summary

    async def poll_binding(
        self, binding: InventorySettingsModel
    ) -> InventoryPollSummary:
        summary = InventoryPollSummary(bindings=1)
        if not (self.automation_enabled and self.poller_enabled and binding.enabled):
            return summary
        control = self.session.scalar(
            select(InventoryProcessingControlModel).where(
                InventoryProcessingControlModel.tenant_id == binding.tenant_id,
                InventoryProcessingControlModel.enabled.is_(True),
                InventoryProcessingControlModel.paused.is_(False),
            )
        )
        if control is None:
            return summary
        source = self.session.scalar(
            select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == binding.tenant_id,
                ExternalSourceModel.id == binding.external_source_id,
            )
        )
        if source is None:
            raise LookupError("inventory_external_source_not_found")
        if source.source_type != "google_drive":
            raise ValueError("inventory_source_must_be_google_drive")
        metadata = source.source_metadata or {}
        connection_id = metadata.get("oauth_connection_id")
        if not isinstance(connection_id, str) or not connection_id:
            raise LookupError("inventory_google_connection_unavailable")

        started = time.monotonic()
        inventory_drive_metrics.increment("poll_started")
        logger.info(
            "inventory_drive_poll_started tenant_id=%s external_source_id=%s inbox_folder_id=%s",
            binding.tenant_id,
            binding.external_source_id,
            binding.inbox_folder_id,
        )
        access_token = await self.token_resolver(connection_id)
        page_token: str | None = None
        source_files = InventorySourceFileRepository(self.session)
        jobs = InventoryJobRepository(self.session, _REGISTERED_PHASE3_JOBS)
        async with self.client_factory(access_token) as drive:
            while True:
                page, page_token = await drive.children_page(
                    binding.inbox_folder_id,
                    page_token=page_token,
                    page_size=200,
                )
                for item in page:
                    summary.files_listed += 1
                    inventory_drive_metrics.increment("files_listed")
                    if is_google_drive_folder(item.mime_type):
                        summary.folders_ignored += 1
                        inventory_drive_metrics.increment("folder_ignored")
                        continue
                    supported = is_supported_inventory_image(item.mime_type)
                    value = InventorySourceFileInput(
                        external_source_id=binding.external_source_id,
                        drive_file_id=item.id,
                        filename=item.name,
                        mime_type=normalize_inventory_mime_type(item.mime_type),
                        drive_modified_time=item.modified_at or _EPOCH,
                        drive_size=item.size,
                        provider_metadata_json={
                            "parent_id": item.parent_id,
                            "provider_modified_time_missing": item.modified_at is None,
                        },
                    )
                    row, created = source_files.register_with_result(
                        binding.tenant_id,
                        value,
                        status="discovered" if supported else "unsupported",
                    )
                    if not created:
                        summary.provider_versions_repeated += 1
                        inventory_drive_metrics.increment("provider_version_duplicate")
                        continue
                    summary.provider_versions_created += 1
                    inventory_drive_metrics.increment("provider_version_created")
                    if not supported:
                        summary.unsupported += 1
                        inventory_drive_metrics.increment("unsupported")
                        row.last_error_code = "unsupported_inventory_mime_type"
                        row.last_error_message = "File MIME type is not supported by Inventory ingestion."
                        continue
                    job = jobs.create_job(
                        tenant_id=binding.tenant_id,
                        job_type=INVENTORY_FILE_DOWNLOAD_JOB,
                        entity_type="inventory_source_file",
                        entity_id=row.id,
                        idempotency_key=f"inventory-file-download:{row.id}",
                        payload={"source_file_id": row.id},
                    )
                    row.status = "queued"
                    summary.jobs_created += 1
                    inventory_drive_metrics.increment("download_job_created")
                    logger.info(
                        "inventory_download_enqueued tenant_id=%s external_source_id=%s source_file_id=%s job_id=%s",
                        binding.tenant_id,
                        binding.external_source_id,
                        row.id,
                        job.id,
                    )
                self.session.flush()
                if not page_token:
                    break
        inventory_drive_metrics.increment("poll_completed")
        logger.info(
            "inventory_drive_poll_completed tenant_id=%s external_source_id=%s files_listed=%s new_versions=%s jobs_created=%s duration_ms=%s",
            binding.tenant_id,
            binding.external_source_id,
            summary.files_listed,
            summary.provider_versions_created,
            summary.jobs_created,
            round((time.monotonic() - started) * 1000),
        )
        return summary


async def poll_inventory_drive(settings: Settings) -> InventoryPollSummary:
    with SessionLocal() as session:
        return await InventoryDrivePoller(
            session,
            automation_enabled=settings.INVENTORY_AUTOMATION_ENABLED,
            poller_enabled=settings.INVENTORY_DRIVE_POLLER_ENABLED,
            allowed_tenant_ids=settings.inventory_tenant_allowlist,
        ).poll_due()
