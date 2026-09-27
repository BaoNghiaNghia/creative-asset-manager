from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.assets.repository import (
    AssetContentConflictError,
    AssetRegistryRepository,
)
from app.modules.realistic_review_ugc.model import (
    RrugcExportModel,
    RrugcGenerationAttemptModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.service import RrugcError
from app.modules.storage.repository import ManagedStorageRepository
from app.providers.google.storage import GoogleDriveAssetStorage


@dataclass(frozen=True, slots=True)
class BatchExportResult:
    scanned: int
    exported: int
    reused: int
    items: tuple[RrugcExportModel, ...]


class RrugcExportService:
    storage_provider = GoogleDriveAssetStorage.provider_name

    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)
        self.assets = AssetRegistryRepository(session)
        self.storage = ManagedStorageRepository(session)

    def export_attempt(
        self,
        *,
        tenant_id: str,
        generation_attempt_id: str,
        user_id: str,
    ) -> tuple[RrugcExportModel, bool]:
        existing = self.repository.export_for_attempt(
            tenant_id,
            generation_attempt_id,
        )
        if existing is not None:
            self._sync_attempt(existing, user_id=None)
            return existing, False

        attempt = self.repository.lock_generation_attempt(
            tenant_id,
            generation_attempt_id,
        )
        if attempt is None:
            raise RrugcError(
                "generation_attempt_not_found",
                "Generation attempt not found.",
                status_code=404,
            )
        self._validate_exportable(attempt)
        review = self.repository.review_task_for_attempt(
            tenant_id,
            generation_attempt_id,
        )
        if review is None or review.status != "approved":
            raise RrugcError(
                "rrugc_export_review_required",
                "An approved review task is required before export.",
                status_code=409,
            )

        content_hash = str(attempt.output_content_hash or "")
        asset = self.assets.find_asset_by_content_hash(tenant_id, content_hash)
        if asset is None:
            try:
                asset = self.assets.create_asset(
                    tenant_id=tenant_id,
                    content_hash=content_hash,
                    mime_type=attempt.output_content_type,
                    size_bytes=attempt.output_size_bytes,
                )
            except AssetContentConflictError:
                asset = self.assets.find_asset_by_content_hash(
                    tenant_id,
                    content_hash,
                )
                if asset is None:
                    raise

        storage = self.storage.get(
            tenant_id,
            asset.id,
            self.storage_provider,
        )
        if storage is None:
            storage = self.storage.get_or_create(
                tenant_id=tenant_id,
                asset_id=asset.id,
                content_hash=content_hash,
                storage_provider=self.storage_provider,
                storage_class="durable",
            )
            self.storage.mark_stored(
                storage,
                remote_file_id=str(attempt.output_remote_file_id),
                remote_folder_id=attempt.output_remote_folder_id,
                web_url=attempt.output_web_url,
            )

        now = datetime.now(timezone.utc)
        row = RrugcExportModel(
            tenant_id=tenant_id,
            campaign_id=attempt.campaign_id,
            generation_attempt_id=attempt.id,
            review_task_id=review.id,
            catalog_asset_id=asset.id,
            content_hash=content_hash,
            content_type=attempt.output_content_type,
            size_bytes=attempt.output_size_bytes,
            storage_provider=self.storage_provider,
            remote_file_id=str(attempt.output_remote_file_id),
            remote_folder_id=attempt.output_remote_folder_id,
            web_url=attempt.output_web_url,
            status="exported",
            requested_by_user_id=user_id,
            exported_at=now,
        )
        try:
            self.session.add(row)
            self.session.flush()
            attempt.export_status = "exported"
            attempt.export_record_id = row.id
            attempt.catalog_asset_id = asset.id
            attempt.exported_by_user_id = user_id
            attempt.exported_at = now
            self.session.commit()
            self.session.refresh(row)
            return row, True
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.export_for_attempt(
                tenant_id,
                generation_attempt_id,
            )
            if existing is None:
                raise
            self._sync_attempt(existing, user_id=None)
            return existing, False

    def export_campaign(
        self,
        *,
        tenant_id: str,
        campaign_id: str,
        user_id: str,
        limit: int = 100,
    ) -> BatchExportResult:
        campaign = self.repository.get_campaign(tenant_id, campaign_id)
        if campaign is None:
            raise RrugcError(
                "campaign_not_found",
                "Campaign not found.",
                status_code=404,
            )
        attempts = self.repository.exportable_attempts(
            tenant_id,
            campaign_id=campaign_id,
            limit=limit,
        )
        rows: list[RrugcExportModel] = []
        exported = 0
        reused = 0
        for attempt in attempts:
            row, created = self.export_attempt(
                tenant_id=tenant_id,
                generation_attempt_id=attempt.id,
                user_id=user_id,
            )
            rows.append(row)
            if created:
                exported += 1
            else:
                reused += 1
        return BatchExportResult(
            scanned=len(attempts),
            exported=exported,
            reused=reused,
            items=tuple(rows),
        )

    def summary(
        self,
        *,
        tenant_id: str,
        campaign_id: str,
    ) -> dict[str, int]:
        if self.repository.get_campaign(tenant_id, campaign_id) is None:
            raise RrugcError(
                "campaign_not_found",
                "Campaign not found.",
                status_code=404,
            )
        return self.repository.campaign_export_summary(
            tenant_id,
            campaign_id,
        )

    @staticmethod
    def _validate_exportable(attempt: RrugcGenerationAttemptModel) -> None:
        if attempt.status != "completed":
            raise RrugcError(
                "rrugc_export_generation_incomplete",
                "Only completed generation outputs can be exported.",
                status_code=409,
            )
        if attempt.review_status != "approved":
            raise RrugcError(
                "rrugc_export_approval_required",
                "Only approved outputs can be exported.",
                status_code=409,
            )
        if attempt.export_status == "exported":
            return
        if attempt.export_status != "export_ready":
            raise RrugcError(
                "rrugc_export_not_ready",
                "Generation output is not export ready.",
                status_code=409,
            )
        if not attempt.output_content_hash or not attempt.output_remote_file_id:
            raise RrugcError(
                "rrugc_export_provenance_incomplete",
                "Stored generation output provenance is incomplete.",
                status_code=409,
            )

    def _sync_attempt(
        self,
        row: RrugcExportModel,
        *,
        user_id: str | None,
    ) -> None:
        attempt = self.repository.lock_generation_attempt(
            row.tenant_id,
            row.generation_attempt_id,
        )
        if attempt is None:
            return
        changed = False
        if attempt.export_status != "exported":
            attempt.export_status = "exported"
            changed = True
        if attempt.export_record_id != row.id:
            attempt.export_record_id = row.id
            changed = True
        if attempt.catalog_asset_id != row.catalog_asset_id:
            attempt.catalog_asset_id = row.catalog_asset_id
            changed = True
        if attempt.exported_by_user_id != row.requested_by_user_id:
            attempt.exported_by_user_id = row.requested_by_user_id
            changed = True
        if attempt.exported_at != row.exported_at:
            attempt.exported_at = row.exported_at
            changed = True
        if changed:
            self.session.commit()
