from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.delivery import RrugcDeliveryService
from app.modules.realistic_review_ugc.model import RrugcDeliveryPackageModel
from app.modules.realistic_review_ugc.service import RrugcError


@dataclass(frozen=True, slots=True)
class DeliveryMaintenanceTickResult:
    tenant_id: str
    job_id: str | None
    created: bool


class RrugcDeliveryMaintenanceScheduler:
    """Periodic producer for durable RRUGC delivery maintenance jobs."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.PROCESSING_JOBS_ENABLED
            and self.settings.MANAGED_ASSET_STORAGE_ENABLED
            and self.settings.RRUGC_DELIVERY_AUTOMATION_ENABLED
        )

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="rrugc-delivery-maintenance",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(
                timeout
                if timeout is not None
                else self.settings.RRUGC_DELIVERY_MAINTENANCE_INTERVAL_SECONDS + 1
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                self.logger.exception("rrugc_delivery_maintenance_tick_failed")
            self._stop.wait(
                self.settings.RRUGC_DELIVERY_MAINTENANCE_INTERVAL_SECONDS
            )

    def enqueue_tenant(
        self,
        tenant_id: str,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> DeliveryMaintenanceTickResult:
        current = now or datetime.now(timezone.utc)
        interval = max(
            60,
            self.settings.RRUGC_DELIVERY_MAINTENANCE_INTERVAL_SECONDS,
        )
        bucket = int(current.timestamp()) // interval
        key = (
            f"rrugc-delivery-maintenance:{tenant_id}:manual:{int(current.timestamp())}"
            if force
            else f"rrugc-delivery-maintenance:{tenant_id}:{bucket}"
        )
        with self.session_factory() as session:
            job, created = ProcessingRepository(session).create_job_once(
                tenant_id=tenant_id,
                job_type="rrugc_delivery_maintenance",
                entity_type="rrugc_delivery_maintenance",
                entity_id=tenant_id,
                idempotency_key=key,
                payload={
                    "limit": self.settings.RRUGC_DELIVERY_MAINTENANCE_MAX_PACKAGES_PER_RUN,
                    "scheduled": not force,
                },
                priority=25,
                max_attempts=3,
                next_attempt_at=current,
            )
            session.commit()
            return DeliveryMaintenanceTickResult(
                tenant_id=tenant_id,
                job_id=job.id,
                created=created,
            )

    def tick(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[DeliveryMaintenanceTickResult, ...]:
        if not self.enabled:
            return ()
        current = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            tenants = tuple(
                session.scalars(
                    select(RrugcDeliveryPackageModel.tenant_id)
                    .where(
                        RrugcDeliveryPackageModel.status.in_(
                            ("delivered", "partial_failed")
                        )
                    )
                    .distinct()
                    .order_by(RrugcDeliveryPackageModel.tenant_id)
                )
            )
        results: list[DeliveryMaintenanceTickResult] = []
        for tenant_id in tenants:
            try:
                result = self.enqueue_tenant(tenant_id, now=current)
                results.append(result)
            except Exception:
                self.logger.exception(
                    "rrugc_delivery_maintenance_schedule_failed",
                    extra={"tenant_id": tenant_id},
                )
                results.append(
                    DeliveryMaintenanceTickResult(
                        tenant_id=tenant_id,
                        job_id=None,
                        created=False,
                    )
                )
        return tuple(results)


class RrugcDeliveryMaintenanceJobHandler:
    def __init__(self, settings: Settings):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        if not self.settings.RRUGC_DELIVERY_AUTOMATION_ENABLED:
            return JobHandlerResult.completed()
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        storage = context.dependencies.storage_provider
        if storage is None or not hasattr(storage, "copy_asset_to_folder"):
            return JobHandlerResult.retryable(
                "managed_storage_unavailable",
                "Managed storage is unavailable for RRUGC delivery maintenance.",
            )
        try:
            return asyncio.run(self._execute(context, storage))
        except Exception:
            context.logger.exception(
                "rrugc_delivery_maintenance_failed",
                extra={"tenant_id": context.job.tenant_id},
            )
            return JobHandlerResult.retryable(
                "rrugc_delivery_maintenance_failed",
                "RRUGC delivery maintenance failed.",
            )

    async def _execute(self, context: JobHandlerContext, storage: object) -> JobHandlerResult:
        limit = int(
            context.job.payload.get("limit")
            or self.settings.RRUGC_DELIVERY_MAINTENANCE_MAX_PACKAGES_PER_RUN
        )
        limit = max(
            1,
            min(limit, self.settings.RRUGC_DELIVERY_MAINTENANCE_MAX_PACKAGES_PER_RUN),
        )

        with context.dependencies.session_factory() as session:
            service = RrugcDeliveryService(
                session,
                storage=storage,  # type: ignore[arg-type]
                settings=self.settings,
            )
            lifecycle = service.reconcile_lifecycle(
                tenant_id=context.job.tenant_id,
                limit=limit,
            )
            package_ids = [
                row.id
                for row in service.due_retry_packages(
                    tenant_id=context.job.tenant_id,
                    limit=limit,
                )
            ]

        retried = 0
        delivered = 0
        exhausted = 0
        for package_id in package_ids:
            if context.is_cancelled:
                return JobHandlerResult.cancelled()
            with context.dependencies.session_factory() as session:
                service = RrugcDeliveryService(
                    session,
                    storage=storage,  # type: ignore[arg-type]
                    settings=self.settings,
                )
                try:
                    package = await service.retry_package(
                        tenant_id=context.job.tenant_id,
                        package_id=package_id,
                        automated_retry=True,
                    )
                except RrugcError as exc:
                    if exc.retryable:
                        return JobHandlerResult.retryable(
                            exc.code,
                            "RRUGC delivery retry is temporarily unavailable.",
                        )
                    service.exhaust_auto_retry(
                        tenant_id=context.job.tenant_id,
                        package_id=package_id,
                        reason_code=exc.code,
                        reason_message=(
                            "Automatic delivery retry stopped because its destination "
                            "or source is no longer available."
                        ),
                    )
                    exhausted += 1
                    continue
                retried += 1
                if package.status == "delivered":
                    delivered += 1
                elif (
                    package.status == "partial_failed"
                    and package.next_retry_at is None
                ):
                    exhausted += 1

        context.logger.info(
            "rrugc_delivery_maintenance_completed",
            extra={
                "tenant_id": context.job.tenant_id,
                "expired": lifecycle.expired,
                "retry_candidates": len(package_ids),
                "retried": retried,
                "delivered": delivered,
                "exhausted": exhausted,
            },
        )
        return JobHandlerResult.completed()
