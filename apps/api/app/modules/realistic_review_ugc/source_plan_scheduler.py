from __future__ import annotations

import asyncio
import logging
import threading
from typing import Awaitable, Callable

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel
from app.modules.realistic_review_ugc.source_plans import (
    RRUGC_SOURCE_ROOT_FOLDER_ID, RRUGC_SOURCE_PLAN_JOB_TYPE,
    SourcePlanSyncResult,
    sync_source_plans,
)
from app.modules.storage.managed_oauth import MANAGED_STORAGE_OAUTH_PROVIDER
from app.modules.realistic_review_ugc.workspace import TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID

# A small bounded inflight window prevents TikTokShop's large Drive import
# from swamping the shared Gemini lane or starving GatorHats analysis.
TIKTOKSHOP_ANALYSIS_INFLIGHT_LIMIT = 12

SourcePlanSyncer = Callable[..., Awaitable[SourcePlanSyncResult]]


def tiktokshop_source_analysis_budget(session: Session, tenant_id: str) -> int:
    """Tenant+folder scoped in-flight ceiling shared by API and scheduler."""
    active = session.scalar(
        select(func.count(ProcessingJobModel.id))
        .join(
            RrugcSourcePlanModel,
            and_(
                RrugcSourcePlanModel.id == ProcessingJobModel.entity_id,
                RrugcSourcePlanModel.tenant_id == ProcessingJobModel.tenant_id,
            ),
        )
        .where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == RRUGC_SOURCE_PLAN_JOB_TYPE,
            ProcessingJobModel.status.in_(("pending", "retry", "processing")),
            RrugcSourcePlanModel.root_folder_id == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
        )
    ) or 0
    return max(0, TIKTOKSHOP_ANALYSIS_INFLIGHT_LIMIT - int(active))


class RrugcSourcePlanSyncScheduler:
    """Background Drive scanner for RRUGC source-image plans.

    The operational worker scans the configured embroidery source tree even
    when nobody has the RRUGC page open. sync_source_plans is idempotent, so
    unchanged Drive files do not create duplicate plans or analysis jobs.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
        syncer: SourcePlanSyncer = sync_source_plans,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self.syncer = syncer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._idle_scan_streak = 0

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.PROCESSING_JOBS_ENABLED
            and self.settings.MANAGED_ASSET_STORAGE_ENABLED
            and self.settings.RRUGC_SOURCE_AUTO_SYNC_ENABLED
        )

    @property
    def interval_seconds(self) -> int:
        return max(60, int(self.settings.RRUGC_SOURCE_AUTO_SYNC_INTERVAL_SECONDS))

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="rrugc-source-plan-sync-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout if timeout is not None else self.interval_seconds + 1)

    def _next_wait_seconds(self, results: tuple[SourcePlanSyncResult, ...]) -> int:
        # Back off unchanged full-tree scans; a new or updated plan restores
        # the configured interval immediately. Cap delay at 10 minutes.
        unchanged = bool(results) and all(
            item.images_found == item.unchanged
            and not (item.plans_created or item.plans_updated or item.plans_missing or item.jobs_queued)
            for item in results
        )
        self._idle_scan_streak = min(self._idle_scan_streak + 1, 3) if unchanged else 0
        return min(600, self.interval_seconds * (2 ** self._idle_scan_streak))

    def _run(self) -> None:
        while not self._stop.is_set():
            wait_seconds = self.interval_seconds
            try:
                wait_seconds = self._next_wait_seconds(self.tick())
            except Exception:
                self._idle_scan_streak = 0
                self.logger.exception("rrugc_source_auto_sync_tick_failed")
            self._stop.wait(wait_seconds)

    def _tenant_ids(self) -> tuple[str, ...]:
        root_folder_id = str(
            self.settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or ""
        ).strip()
        if not root_folder_id:
            return ()
        with self.session_factory() as session:
            rows = session.scalars(
                select(OAuthConnectionModel)
                .where(
                    OAuthConnectionModel.provider == MANAGED_STORAGE_OAUTH_PROVIDER,
                    OAuthConnectionModel.status.in_(("active", "refresh_error")),
                )
                .order_by(OAuthConnectionModel.updated_at.desc())
            )
            tenant_ids: list[str] = []
            seen: set[str] = set()
            for row in rows:
                configured_root = str(
                    (row.provider_metadata_json or {}).get("root_folder_id") or ""
                ).strip()
                if configured_root != root_folder_id or row.tenant_id in seen:
                    continue
                seen.add(row.tenant_id)
                tenant_ids.append(row.tenant_id)
            return tuple(tenant_ids)

    def _tiktokshop_analysis_budget(self, session: Session, tenant_id: str) -> int:
        return tiktokshop_source_analysis_budget(session, tenant_id)

    def tick(self) -> tuple[SourcePlanSyncResult, ...]:
        if not self.enabled:
            return ()
        results: list[SourcePlanSyncResult] = []
        for tenant_id in self._tenant_ids():
            for root_folder_id in (
                RRUGC_SOURCE_ROOT_FOLDER_ID, TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
            ):
                try:
                    with self.session_factory() as session:
                        options = {}
                        if root_folder_id == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID:
                            budget = self._tiktokshop_analysis_budget(session, tenant_id)
                            if not budget:
                                continue
                            options["analysis_job_limit"] = budget
                        result = asyncio.run(
                            self.syncer(
                                session,
                                tenant_id=tenant_id,
                                user_id="system:rrugc-source-auto-sync",
                                settings=self.settings,
                                root_folder_id=root_folder_id,
                                **options,
                            )
                        )
                    results.append(result)
                    self.logger.info(
                        "rrugc_source_auto_sync_complete",
                        extra={
                            "tenant_id": tenant_id,
                            "root_folder_id": result.root_folder_id,
                            "images_found": result.images_found,
                            "plans_created": result.plans_created,
                            "plans_updated": result.plans_updated,
                            "plans_missing": result.plans_missing,
                            "jobs_queued": result.jobs_queued,
                            "unchanged": result.unchanged,
                        },
                    )
                except Exception as exc:
                    # A TikTokShop outage must not stop GatorHats (or vice
                    # versa), and a failed scan must not mark source files
                    # missing or discard any previous analysis/output.
                    self.logger.exception(
                        "rrugc_source_auto_sync_tenant_failed",
                        extra={
                            "tenant_id": tenant_id,
                            "root_folder_id": root_folder_id,
                            "error_code": type(exc).__name__,
                        },
                    )
        return tuple(results)
