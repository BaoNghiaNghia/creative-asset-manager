from __future__ import annotations

import asyncio
import logging
import threading
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.realistic_review_ugc.source_plans import (
    RRUGC_SOURCE_ROOT_FOLDER_ID,
    SourcePlanSyncResult,
    sync_source_plans,
)
from app.modules.storage.managed_oauth import MANAGED_STORAGE_OAUTH_PROVIDER

SourcePlanSyncer = Callable[..., Awaitable[SourcePlanSyncResult]]


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

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                self.logger.exception("rrugc_source_auto_sync_tick_failed")
            self._stop.wait(self.interval_seconds)

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

    def tick(self) -> tuple[SourcePlanSyncResult, ...]:
        if not self.enabled:
            return ()
        results: list[SourcePlanSyncResult] = []
        for tenant_id in self._tenant_ids():
            try:
                with self.session_factory() as session:
                    result = asyncio.run(
                        self.syncer(
                            session,
                            tenant_id=tenant_id,
                            user_id="system:rrugc-source-auto-sync",
                            settings=self.settings,
                            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
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
                self.logger.exception(
                    "rrugc_source_auto_sync_tenant_failed",
                    extra={
                        "tenant_id": tenant_id,
                        "root_folder_id": RRUGC_SOURCE_ROOT_FOLDER_ID,
                        "error_code": type(exc).__name__,
                    },
                )
        return tuple(results)
