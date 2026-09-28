from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.providers.contracts import StorageProviderError
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcDeliveryDestinationModel,
    RrugcDeliveryEventModel,
    RrugcDeliveryItemModel,
    RrugcDeliveryPackageModel,
    RrugcExportModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.service import RrugcError
from app.providers.google.storage import GoogleDriveAssetStorage


@dataclass(frozen=True, slots=True)
class LifecycleReconcileResult:
    scanned: int
    expired: int


class RrugcDeliveryService:
    def __init__(
        self,
        session: Session,
        storage: GoogleDriveAssetStorage | None = None,
        settings: Settings | None = None,
    ):
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()
        self.repository = RrugcRepository(session)

    def create_destination(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        kind: str,
        target_ref: str,
        retention_days: int,
    ) -> RrugcDeliveryDestinationModel:
        if kind != "google_drive_folder":
            raise RrugcError(
                "rrugc_delivery_destination_kind_unsupported",
                "Only Google Drive folder delivery is supported.",
            )
        clean_name = name.strip()
        clean_target = target_ref.strip()
        if not clean_name or not clean_target:
            raise RrugcError(
                "rrugc_delivery_destination_invalid",
                "Delivery destination name and Drive folder ID are required.",
            )
        existing = self.session.scalar(
            select(RrugcDeliveryDestinationModel).where(
                RrugcDeliveryDestinationModel.tenant_id == tenant_id,
                RrugcDeliveryDestinationModel.name == clean_name,
            )
        )
        if existing is not None:
            if (
                existing.kind == kind
                and existing.target_ref == clean_target
                and existing.retention_days == retention_days
                and existing.active
            ):
                return existing
            raise RrugcError(
                "rrugc_delivery_destination_name_conflict",
                "A delivery destination with this name already exists.",
                status_code=409,
            )
        row = RrugcDeliveryDestinationModel(
            tenant_id=tenant_id,
            name=clean_name,
            kind=kind,
            target_ref=clean_target,
            retention_days=retention_days,
            active=True,
            created_by_user_id=user_id,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def list_destinations(
        self,
        *,
        tenant_id: str,
        include_archived: bool = False,
    ) -> list[RrugcDeliveryDestinationModel]:
        statement = select(RrugcDeliveryDestinationModel).where(
            RrugcDeliveryDestinationModel.tenant_id == tenant_id
        )
        if not include_archived:
            statement = statement.where(
                RrugcDeliveryDestinationModel.active.is_(True)
            )
        return list(
            self.session.scalars(
                statement.order_by(
                    RrugcDeliveryDestinationModel.active.desc(),
                    RrugcDeliveryDestinationModel.updated_at.desc(),
                    RrugcDeliveryDestinationModel.id.desc(),
                )
            )
        )

    def get_destination(
        self,
        *,
        tenant_id: str,
        destination_id: str,
    ) -> RrugcDeliveryDestinationModel | None:
        return self.session.scalar(
            select(RrugcDeliveryDestinationModel).where(
                RrugcDeliveryDestinationModel.tenant_id == tenant_id,
                RrugcDeliveryDestinationModel.id == destination_id,
            )
        )

    def archive_destination(
        self,
        *,
        tenant_id: str,
        destination_id: str,
    ) -> RrugcDeliveryDestinationModel:
        row = self.get_destination(
            tenant_id=tenant_id,
            destination_id=destination_id,
        )
        if row is None:
            raise RrugcError(
                "rrugc_delivery_destination_not_found",
                "Delivery destination not found.",
                status_code=404,
            )
        if not row.active:
            return row
        active_policy = self.session.scalar(
            select(RrugcCampaignModel.id).where(
                RrugcCampaignModel.tenant_id == tenant_id,
                RrugcCampaignModel.auto_complete_on_delivery.is_(True),
                RrugcCampaignModel.completion_destination_id == destination_id,
                RrugcCampaignModel.completed_at.is_(None),
            ).limit(1)
        )
        if active_policy is not None:
            raise RrugcError(
                "rrugc_delivery_destination_in_use",
                "Disable campaign auto-completion before archiving this destination.",
                status_code=409,
            )
        row.active = False
        row.archived_at = datetime.now(timezone.utc)
        self.session.commit()
        self.session.refresh(row)
        return row

    def set_campaign_policy(
        self,
        *,
        tenant_id: str,
        campaign_id: str,
        auto_complete_on_delivery: bool,
        completion_destination_id: str | None,
    ) -> RrugcCampaignModel:
        campaign = self.repository.lock_campaign(tenant_id, campaign_id)
        if campaign is None:
            raise RrugcError(
                "campaign_not_found",
                "Campaign not found.",
                status_code=404,
            )
        destination_id = (
            str(completion_destination_id or "").strip() or None
        )
        if auto_complete_on_delivery:
            if destination_id is None:
                raise RrugcError(
                    "rrugc_completion_destination_required",
                    "Choose an active delivery destination for auto-completion.",
                    status_code=409,
                )
            destination = self.get_destination(
                tenant_id=tenant_id,
                destination_id=destination_id,
            )
            if destination is None or not destination.active:
                raise RrugcError(
                    "rrugc_completion_destination_invalid",
                    "Completion destination is unavailable.",
                    status_code=409,
                )
        campaign.auto_complete_on_delivery = auto_complete_on_delivery
        campaign.completion_destination_id = destination_id
        if not auto_complete_on_delivery and campaign.status == "completed":
            # Completed is durable history. Disabling policy does not reopen the campaign.
            pass
        self.session.commit()
        self.session.refresh(campaign)
        return campaign

    def list_packages(
        self,
        *,
        tenant_id: str,
        campaign_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[RrugcDeliveryPackageModel], int]:
        statement = select(RrugcDeliveryPackageModel).where(
            RrugcDeliveryPackageModel.tenant_id == tenant_id
        )
        if campaign_id is not None:
            statement = statement.where(
                RrugcDeliveryPackageModel.campaign_id == campaign_id
            )
        total = int(
            self.session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        rows = list(
            self.session.scalars(
                statement.order_by(
                    RrugcDeliveryPackageModel.created_at.desc(),
                    RrugcDeliveryPackageModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return rows, total

    def package_items(
        self,
        *,
        tenant_id: str,
        package_id: str,
    ) -> list[RrugcDeliveryItemModel]:
        return list(
            self.session.scalars(
                select(RrugcDeliveryItemModel)
                .where(
                    RrugcDeliveryItemModel.tenant_id == tenant_id,
                    RrugcDeliveryItemModel.package_id == package_id,
                )
                .order_by(RrugcDeliveryItemModel.created_at.asc())
            )
        )

    async def deliver_campaign(
        self,
        *,
        tenant_id: str,
        campaign_id: str,
        destination_id: str,
        user_id: str,
    ) -> RrugcDeliveryPackageModel:
        campaign = self.repository.get_campaign(tenant_id, campaign_id)
        if campaign is None:
            raise RrugcError(
                "campaign_not_found",
                "Campaign not found.",
                status_code=404,
            )
        destination = self.get_destination(
            tenant_id=tenant_id,
            destination_id=destination_id,
        )
        if destination is None or not destination.active:
            raise RrugcError(
                "rrugc_delivery_destination_unavailable",
                "Delivery destination is unavailable.",
                status_code=409,
            )
        if destination.kind != "google_drive_folder":
            raise RrugcError(
                "rrugc_delivery_destination_kind_unsupported",
                "Delivery destination type is unsupported.",
                status_code=409,
            )
        if self.storage is None:
            raise RrugcError(
                "managed_storage_unavailable",
                "Managed Google Drive is unavailable.",
                status_code=503,
                retryable=True,
            )

        exports = list(
            self.session.scalars(
                select(RrugcExportModel)
                .where(
                    RrugcExportModel.tenant_id == tenant_id,
                    RrugcExportModel.campaign_id == campaign_id,
                    RrugcExportModel.status == "exported",
                )
                .order_by(
                    RrugcExportModel.exported_at.asc(),
                    RrugcExportModel.id.asc(),
                )
            )
        )
        if not exports:
            raise RrugcError(
                "rrugc_delivery_no_cataloged_assets",
                "Catalog at least one approved output before delivery.",
                status_code=409,
            )

        key_payload = {
            "campaign_id": campaign_id,
            "destination_id": destination.id,
            "destination_target": destination.target_ref,
            "exports": [
                {"id": row.id, "content_hash": row.content_hash}
                for row in exports
            ],
        }
        idempotency_key = hashlib.sha256(
            json.dumps(
                key_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        package = self.session.scalar(
            select(RrugcDeliveryPackageModel).where(
                RrugcDeliveryPackageModel.tenant_id == tenant_id,
                RrugcDeliveryPackageModel.idempotency_key == idempotency_key,
            )
        )
        if package is None:
            package = RrugcDeliveryPackageModel(
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                destination_id=destination.id,
                idempotency_key=idempotency_key,
                status="pending",
                export_count=len(exports),
                manifest_json=[
                    {
                        "export_id": row.id,
                        "catalog_asset_id": row.catalog_asset_id,
                        "content_hash": row.content_hash,
                        "content_type": row.content_type,
                        "size_bytes": row.size_bytes,
                    }
                    for row in exports
                ],
                created_by_user_id=user_id,
            )
            self.session.add(package)
            self.session.flush()
            for row in exports:
                self.session.add(
                    RrugcDeliveryItemModel(
                        tenant_id=tenant_id,
                        package_id=package.id,
                        export_id=row.id,
                        catalog_asset_id=row.catalog_asset_id,
                        source_remote_file_id=row.remote_file_id,
                        status="pending",
                    )
                )
            try:
                self.session.commit()
            except IntegrityError:
                self.session.rollback()
                package = self.session.scalar(
                    select(RrugcDeliveryPackageModel).where(
                        RrugcDeliveryPackageModel.tenant_id == tenant_id,
                        RrugcDeliveryPackageModel.idempotency_key == idempotency_key,
                    )
                )
                if package is None:
                    raise

        return await self._execute_package(
            package=package,
            destination=destination,
            campaign=campaign,
            automated_retry=False,
        )

    async def retry_package(
        self,
        *,
        tenant_id: str,
        package_id: str,
        automated_retry: bool,
    ) -> RrugcDeliveryPackageModel:
        package = self.session.scalar(
            select(RrugcDeliveryPackageModel).where(
                RrugcDeliveryPackageModel.tenant_id == tenant_id,
                RrugcDeliveryPackageModel.id == package_id,
            )
        )
        if package is None:
            raise RrugcError(
                "rrugc_delivery_package_not_found",
                "Delivery package not found.",
                status_code=404,
            )
        if package.status in {"delivered", "expired"}:
            return package
        destination = self.get_destination(
            tenant_id=tenant_id,
            destination_id=package.destination_id,
        )
        if destination is None or not destination.active:
            raise RrugcError(
                "rrugc_delivery_destination_unavailable",
                "Delivery destination is unavailable.",
                status_code=409,
            )
        campaign = self.repository.get_campaign(tenant_id, package.campaign_id)
        if campaign is None:
            raise RrugcError(
                "campaign_not_found",
                "Campaign not found.",
                status_code=404,
            )
        return await self._execute_package(
            package=package,
            destination=destination,
            campaign=campaign,
            automated_retry=automated_retry,
        )

    async def _execute_package(
        self,
        *,
        package: RrugcDeliveryPackageModel,
        destination: RrugcDeliveryDestinationModel,
        campaign: RrugcCampaignModel,
        automated_retry: bool,
    ) -> RrugcDeliveryPackageModel:
        if self.storage is None:
            raise RrugcError(
                "managed_storage_unavailable",
                "Managed Google Drive is unavailable.",
                status_code=503,
                retryable=True,
            )
        if package.status in {"delivered", "expired"}:
            return package

        items = self.package_items(
            tenant_id=package.tenant_id,
            package_id=package.id,
        )
        export_ids = [item.export_id for item in items]
        exports = list(
            self.session.scalars(
                select(RrugcExportModel).where(
                    RrugcExportModel.tenant_id == package.tenant_id,
                    RrugcExportModel.id.in_(export_ids),
                )
            )
        ) if export_ids else []
        exports_by_id = {row.id: row for row in exports}
        now = datetime.now(timezone.utc)
        if automated_retry:
            package.auto_retry_count += 1
            package.last_retry_at = now
        package.status = "delivering"
        package.started_at = package.started_at or now
        package.next_retry_at = None
        self.session.commit()

        for item in items:
            if item.status == "delivered":
                continue
            export = exports_by_id.get(item.export_id)
            if export is None:
                item.status = "failed"
                item.last_error_code = "rrugc_delivery_export_missing"
                item.last_error_message = "Catalog export no longer exists."
                self.session.commit()
                continue
            try:
                stored = await self.storage.copy_asset_to_folder(
                    tenant_id=package.tenant_id,
                    asset_id=export.catalog_asset_id,
                    content_hash=export.content_hash,
                    source_remote_file_id=export.remote_file_id,
                    destination_folder_id=destination.target_ref,
                    delivery_item_id=item.id,
                    filename=self._delivery_filename(export),
                )
            except StorageProviderError as exc:
                item.status = "failed"
                item.last_error_code = exc.code or "rrugc_delivery_copy_failed"
                item.last_error_message = str(exc)
                self.session.commit()
                continue

            item.status = "delivered"
            item.delivered_remote_file_id = stored.remote_file_id
            item.delivered_web_url = stored.web_url
            item.delivered_at = datetime.now(timezone.utc)
            item.last_error_code = None
            item.last_error_message = None
            self.session.commit()

        items = self.package_items(
            tenant_id=package.tenant_id,
            package_id=package.id,
        )
        package.delivered_count = sum(item.status == "delivered" for item in items)
        package.failed_count = sum(item.status == "failed" for item in items)
        completed_at = datetime.now(timezone.utc)
        if package.delivered_count == package.export_count:
            package.status = "delivered"
            package.delivered_at = completed_at
            package.expires_at = completed_at + timedelta(
                days=destination.retention_days
            )
            package.next_retry_at = None
            self._record_event(
                package=package,
                event_type="package_delivered",
                severity="info",
                message=(
                    f"Delivered {package.delivered_count}/{package.export_count} "
                    "cataloged assets."
                ),
                key_suffix=f"delivered:{package.delivered_count}",
                payload={
                    "delivered_count": package.delivered_count,
                    "export_count": package.export_count,
                    "destination_id": package.destination_id,
                    "automated_retry": automated_retry,
                },
            )
        else:
            package.status = "partial_failed"
            automation_enabled = bool(
                self.settings.RRUGC_DELIVERY_AUTOMATION_ENABLED
                and self.settings.PROCESSING_JOBS_ENABLED
                and self.settings.MANAGED_ASSET_STORAGE_ENABLED
            )
            retry_exhausted = (
                package.auto_retry_count
                >= self.settings.RRUGC_DELIVERY_AUTO_RETRY_MAX_ATTEMPTS
            )
            if automation_enabled and not retry_exhausted:
                package.next_retry_at = completed_at + timedelta(
                    seconds=self._retry_delay_seconds(package.auto_retry_count)
                )
            else:
                package.next_retry_at = None
            self._record_event(
                package=package,
                event_type=(
                    "auto_retry_exhausted"
                    if automation_enabled and retry_exhausted
                    else "package_partial_failed"
                ),
                severity=(
                    "error"
                    if automation_enabled and retry_exhausted
                    else "warning"
                ),
                message=(
                    f"Delivery has {package.failed_count} failed item(s); "
                    + (
                        "automatic retry budget exhausted."
                        if automation_enabled and retry_exhausted
                        else (
                            "automatic retry is scheduled."
                            if package.next_retry_at is not None
                            else "retry remains manual while automation is disabled."
                        )
                    )
                ),
                key_suffix=(
                    f"partial:{package.auto_retry_count}:{package.failed_count}"
                ),
                payload={
                    "failed_count": package.failed_count,
                    "delivered_count": package.delivered_count,
                    "export_count": package.export_count,
                    "auto_retry_count": package.auto_retry_count,
                    "next_retry_at": (
                        package.next_retry_at.isoformat()
                        if package.next_retry_at is not None
                        else None
                    ),
                },
            )
        self.session.commit()
        self.session.refresh(package)

        if package.status == "delivered":
            self._auto_complete_campaign(
                campaign=campaign,
                package=package,
            )
        return package

    def delivery_summary(
        self,
        *,
        tenant_id: str,
        campaign_id: str,
    ) -> dict[str, object]:
        campaign = self.repository.get_campaign(tenant_id, campaign_id)
        if campaign is None:
            raise RrugcError(
                "campaign_not_found",
                "Campaign not found.",
                status_code=404,
            )
        export_summary = self.repository.campaign_export_summary(
            tenant_id,
            campaign_id,
        )
        package_base = (
            RrugcDeliveryPackageModel.tenant_id == tenant_id,
            RrugcDeliveryPackageModel.campaign_id == campaign_id,
        )

        def package_count(status: str | None = None) -> int:
            statement = (
                select(func.count())
                .select_from(RrugcDeliveryPackageModel)
                .where(*package_base)
            )
            if status is not None:
                statement = statement.where(
                    RrugcDeliveryPackageModel.status == status
                )
            return int(self.session.scalar(statement) or 0)

        latest = self.session.scalar(
            select(RrugcDeliveryPackageModel)
            .where(*package_base)
            .order_by(
                RrugcDeliveryPackageModel.created_at.desc(),
                RrugcDeliveryPackageModel.id.desc(),
            )
            .limit(1)
        )
        completion_package = None
        if campaign.completion_destination_id:
            completion_package = self.session.scalar(
                select(RrugcDeliveryPackageModel)
                .where(
                    *package_base,
                    RrugcDeliveryPackageModel.destination_id
                    == campaign.completion_destination_id,
                    RrugcDeliveryPackageModel.status == "delivered",
                )
                .order_by(
                    RrugcDeliveryPackageModel.delivered_at.desc(),
                    RrugcDeliveryPackageModel.id.desc(),
                )
                .limit(1)
            )
        eligible = bool(
            campaign.auto_complete_on_delivery
            and campaign.completion_destination_id
            and completion_package is not None
            and completion_package.export_count == export_summary["exported"]
            and export_summary["exported"] > 0
            and export_summary["export_ready"] == 0
            and export_summary["review_pending"] == 0
        )
        return {
            "campaign_status": campaign.status,
            "auto_complete_on_delivery": campaign.auto_complete_on_delivery,
            "completion_destination_id": campaign.completion_destination_id,
            "cataloged": export_summary["exported"],
            "packages_total": package_count(),
            "packages_delivered": package_count("delivered"),
            "packages_partial_failed": package_count("partial_failed"),
            "packages_expired": package_count("expired"),
            "latest_delivered_count": latest.delivered_count if latest else 0,
            "latest_export_count": latest.export_count if latest else 0,
            "auto_complete_eligible": eligible,
            "completed_at": campaign.completed_at,
        }

    def due_retry_packages(
        self,
        *,
        tenant_id: str,
        limit: int,
        now: datetime | None = None,
    ) -> list[RrugcDeliveryPackageModel]:
        current = now or datetime.now(timezone.utc)
        legacy_retry_cutoff = current - timedelta(
            seconds=self.settings.RRUGC_DELIVERY_AUTO_RETRY_BASE_SECONDS
        )
        retry_due = or_(
            and_(
                RrugcDeliveryPackageModel.next_retry_at.is_not(None),
                RrugcDeliveryPackageModel.next_retry_at <= current,
            ),
            and_(
                RrugcDeliveryPackageModel.next_retry_at.is_(None),
                RrugcDeliveryPackageModel.auto_retry_count == 0,
                RrugcDeliveryPackageModel.updated_at <= legacy_retry_cutoff,
            ),
        )
        return list(
            self.session.scalars(
                select(RrugcDeliveryPackageModel)
                .where(
                    RrugcDeliveryPackageModel.tenant_id == tenant_id,
                    RrugcDeliveryPackageModel.status == "partial_failed",
                    retry_due,
                    RrugcDeliveryPackageModel.auto_retry_count
                    < self.settings.RRUGC_DELIVERY_AUTO_RETRY_MAX_ATTEMPTS,
                )
                .order_by(
                    RrugcDeliveryPackageModel.updated_at.asc(),
                    RrugcDeliveryPackageModel.id.asc(),
                )
                .limit(limit)
            )
        )

    def exhaust_auto_retry(
        self,
        *,
        tenant_id: str,
        package_id: str,
        reason_code: str,
        reason_message: str,
    ) -> RrugcDeliveryPackageModel | None:
        package = self.session.scalar(
            select(RrugcDeliveryPackageModel).where(
                RrugcDeliveryPackageModel.tenant_id == tenant_id,
                RrugcDeliveryPackageModel.id == package_id,
            )
        )
        if package is None:
            return None
        package.auto_retry_count = max(
            package.auto_retry_count,
            self.settings.RRUGC_DELIVERY_AUTO_RETRY_MAX_ATTEMPTS,
        )
        package.next_retry_at = None
        self._record_event(
            package=package,
            event_type="auto_retry_exhausted",
            severity="error",
            message=reason_message,
            key_suffix=f"retry-exhausted:{reason_code}",
            payload={
                "reason_code": reason_code,
                "auto_retry_count": package.auto_retry_count,
            },
        )
        self.session.commit()
        self.session.refresh(package)
        return package

    def recent_events(
        self,
        *,
        tenant_id: str,
        limit: int = 20,
    ) -> list[RrugcDeliveryEventModel]:
        return list(
            self.session.scalars(
                select(RrugcDeliveryEventModel)
                .where(RrugcDeliveryEventModel.tenant_id == tenant_id)
                .order_by(
                    RrugcDeliveryEventModel.created_at.desc(),
                    RrugcDeliveryEventModel.id.desc(),
                )
                .limit(limit)
            )
        )

    def operations_summary(
        self,
        *,
        tenant_id: str,
        now: datetime | None = None,
    ) -> dict[str, object]:
        current = now or datetime.now(timezone.utc)

        def count(model: type, *filters: object) -> int:
            return int(
                self.session.scalar(
                    select(func.count()).select_from(model).where(*filters)
                )
                or 0
            )

        package_base = (RrugcDeliveryPackageModel.tenant_id == tenant_id,)
        item_base = (RrugcDeliveryItemModel.tenant_id == tenant_id,)
        campaign_base = (RrugcCampaignModel.tenant_id == tenant_id,)
        destination_base = (RrugcDeliveryDestinationModel.tenant_id == tenant_id,)
        max_retries = self.settings.RRUGC_DELIVERY_AUTO_RETRY_MAX_ATTEMPTS
        automation_enabled = bool(
            self.settings.RRUGC_DELIVERY_AUTOMATION_ENABLED
            and self.settings.PROCESSING_JOBS_ENABLED
            and self.settings.MANAGED_ASSET_STORAGE_ENABLED
        )
        legacy_retry_cutoff = current - timedelta(
            seconds=self.settings.RRUGC_DELIVERY_AUTO_RETRY_BASE_SECONDS
        )
        retry_due_filter = or_(
            and_(
                RrugcDeliveryPackageModel.next_retry_at.is_not(None),
                RrugcDeliveryPackageModel.next_retry_at <= current,
            ),
            and_(
                RrugcDeliveryPackageModel.next_retry_at.is_(None),
                RrugcDeliveryPackageModel.auto_retry_count == 0,
                RrugcDeliveryPackageModel.updated_at <= legacy_retry_cutoff,
            ),
        )
        latest_delivery_at = self.session.scalar(
            select(func.max(RrugcDeliveryPackageModel.delivered_at)).where(
                RrugcDeliveryPackageModel.tenant_id == tenant_id
            )
        )
        return {
            "automation_enabled": automation_enabled,
            "campaigns_total": count(RrugcCampaignModel, *campaign_base),
            "campaigns_completed": count(
                RrugcCampaignModel,
                *campaign_base,
                RrugcCampaignModel.completed_at.is_not(None),
            ),
            "destinations_active": count(
                RrugcDeliveryDestinationModel,
                *destination_base,
                RrugcDeliveryDestinationModel.active.is_(True),
            ),
            "packages_total": count(RrugcDeliveryPackageModel, *package_base),
            "packages_delivered": count(
                RrugcDeliveryPackageModel,
                *package_base,
                RrugcDeliveryPackageModel.status == "delivered",
            ),
            "packages_partial_failed": count(
                RrugcDeliveryPackageModel,
                *package_base,
                RrugcDeliveryPackageModel.status == "partial_failed",
            ),
            "packages_expired": count(
                RrugcDeliveryPackageModel,
                *package_base,
                RrugcDeliveryPackageModel.status == "expired",
            ),
            "retry_due": (
                count(
                    RrugcDeliveryPackageModel,
                    *package_base,
                    RrugcDeliveryPackageModel.status == "partial_failed",
                    retry_due_filter,
                    RrugcDeliveryPackageModel.auto_retry_count < max_retries,
                )
                if automation_enabled
                else 0
            ),
            "retry_exhausted": count(
                RrugcDeliveryPackageModel,
                *package_base,
                RrugcDeliveryPackageModel.status == "partial_failed",
                RrugcDeliveryPackageModel.auto_retry_count >= max_retries,
            ),
            "items_delivered": count(
                RrugcDeliveryItemModel,
                *item_base,
                RrugcDeliveryItemModel.status == "delivered",
            ),
            "items_failed": count(
                RrugcDeliveryItemModel,
                *item_base,
                RrugcDeliveryItemModel.status == "failed",
            ),
            "latest_delivery_at": latest_delivery_at,
            "maintenance_interval_seconds": (
                self.settings.RRUGC_DELIVERY_MAINTENANCE_INTERVAL_SECONDS
            ),
            "auto_retry_max_attempts": max_retries,
        }

    def _retry_delay_seconds(self, auto_retry_count: int) -> int:
        exponent = max(0, int(auto_retry_count))
        delay = self.settings.RRUGC_DELIVERY_AUTO_RETRY_BASE_SECONDS * (2 ** exponent)
        return min(delay, self.settings.RRUGC_DELIVERY_AUTO_RETRY_MAX_SECONDS)

    def _record_event(
        self,
        *,
        package: RrugcDeliveryPackageModel,
        event_type: str,
        severity: str,
        message: str,
        key_suffix: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        key = f"rrugc-delivery:{package.id}:{key_suffix}"
        existing = self.session.scalar(
            select(RrugcDeliveryEventModel.id).where(
                RrugcDeliveryEventModel.tenant_id == package.tenant_id,
                RrugcDeliveryEventModel.idempotency_key == key,
            )
        )
        if existing is not None:
            return
        try:
            with self.session.begin_nested():
                self.session.add(
                    RrugcDeliveryEventModel(
                        tenant_id=package.tenant_id,
                        campaign_id=package.campaign_id,
                        package_id=package.id,
                        event_type=event_type,
                        severity=severity,
                        message=message[:500],
                        payload_json=dict(payload or {}),
                        idempotency_key=key,
                    )
                )
                self.session.flush()
        except IntegrityError:
            pass

    def reconcile_lifecycle(
        self,
        *,
        tenant_id: str,
        limit: int = 200,
        now: datetime | None = None,
    ) -> LifecycleReconcileResult:
        current = now or datetime.now(timezone.utc)
        rows = list(
            self.session.scalars(
                select(RrugcDeliveryPackageModel)
                .where(
                    RrugcDeliveryPackageModel.tenant_id == tenant_id,
                    RrugcDeliveryPackageModel.status == "delivered",
                    RrugcDeliveryPackageModel.expires_at.is_not(None),
                    RrugcDeliveryPackageModel.expires_at <= current,
                )
                .order_by(RrugcDeliveryPackageModel.expires_at.asc())
                .limit(limit)
            )
        )
        for row in rows:
            row.status = "expired"
            row.expired_at = current
            row.next_retry_at = None
            self._record_event(
                package=row,
                event_type="package_expired",
                severity="info",
                message="Delivery package retention window expired.",
                key_suffix="expired",
                payload={
                    "expires_at": row.expires_at.isoformat()
                    if row.expires_at is not None
                    else None,
                },
            )
        if rows:
            self.session.commit()
        return LifecycleReconcileResult(
            scanned=len(rows),
            expired=len(rows),
        )

    def _auto_complete_campaign(
        self,
        *,
        campaign: RrugcCampaignModel,
        package: RrugcDeliveryPackageModel,
    ) -> None:
        if (
            not campaign.auto_complete_on_delivery
            or campaign.completion_destination_id != package.destination_id
        ):
            return
        summary = self.repository.campaign_export_summary(
            campaign.tenant_id,
            campaign.id,
        )
        if not (
            summary["exported"] > 0
            and summary["export_ready"] == 0
            and summary["review_pending"] == 0
            and package.export_count == summary["exported"]
            and package.delivered_count == package.export_count
        ):
            return
        campaign.status = "completed"
        campaign.completed_at = datetime.now(timezone.utc)
        self._record_event(
            package=package,
            event_type="campaign_completed",
            severity="info",
            message="Campaign completed after full delivery to its configured destination.",
            key_suffix="campaign-completed",
            payload={
                "campaign_id": campaign.id,
                "destination_id": package.destination_id,
                "completed_at": campaign.completed_at.isoformat(),
            },
        )
        self.session.commit()
        self.session.refresh(campaign)

    @staticmethod
    def _delivery_filename(row: RrugcExportModel) -> str:
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(str(row.content_type or "").lower(), "")
        return f"rrugc-{row.campaign_id[:8]}-{row.id[:8]}{suffix}"
