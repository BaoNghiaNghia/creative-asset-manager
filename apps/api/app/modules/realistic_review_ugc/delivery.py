from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.providers.contracts import StorageProviderError
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcDeliveryDestinationModel,
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
    ):
        self.session = session
        self.storage = storage
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

        if package.status in {"delivered", "expired"}:
            return package

        items = self.package_items(
            tenant_id=tenant_id,
            package_id=package.id,
        )
        exports_by_id = {row.id: row for row in exports}
        now = datetime.now(timezone.utc)
        package.status = "delivering"
        package.started_at = package.started_at or now
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
                    tenant_id=tenant_id,
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
            tenant_id=tenant_id,
            package_id=package.id,
        )
        package.delivered_count = sum(item.status == "delivered" for item in items)
        package.failed_count = sum(item.status == "failed" for item in items)
        if package.delivered_count == package.export_count:
            package.status = "delivered"
            package.delivered_at = datetime.now(timezone.utc)
            package.expires_at = package.delivered_at + timedelta(
                days=destination.retention_days
            )
        else:
            package.status = "partial_failed"
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
