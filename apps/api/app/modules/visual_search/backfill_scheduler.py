from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.processing.repository import ProcessingRepository
from app.modules.visual_search.backfill_control import VisualSearchBackfillController
from app.modules.visual_search.backfill_executor import VisualSearchBackfillExecutor
from app.modules.visual_search.backfill_repository import VisualSearchBackfillRunRepository
from app.modules.visual_search.backfill_policy import VisualBackfillPolicy, active_visual_queue_depth
from app.modules.visual_search.failed_job_recovery import recover_failed_visual_jobs
from app.modules.visual_search.lifecycle import VISUAL_EMBEDDING_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class VisualBackfillScheduleResult:
    tenant_id: str
    run_id: str | None
    status: str
    scanned: int = 0
    enqueued: int = 0
    recovered: int = 0
    throttled: bool = False


class VisualSearchBackfillScheduler:
    """Continuously advance durable historical Visual Search backfill runs.

    The scheduler is hosted only by the dedicated visual worker. It creates an
    initial run (or a run for a new embedding schema), but it never silently
    restarts a paused, failed, cancelled, or completed current-schema run.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        index,
        *,
        async_executor=None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.index = index
        self.async_executor = async_executor
        self.logger = logger or logging.getLogger("cam.visual_backfill")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def tenant_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    value.strip()
                    for value in self.settings.VISUAL_SEARCH_CANARY_TENANT_IDS.split(",")
                    if value.strip()
                }
            )
        )

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.PROCESSING_JOBS_ENABLED
            and self.settings.VISUAL_SEARCH_ENABLED
            and self.settings.VISUAL_SEARCH_BACKFILL_ENABLED
            and self.index is not None
            and self.tenant_ids
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        if not self.enabled:
            self.logger.info(
                "visual_backfill_scheduler_disabled",
                extra={
                    "processing_enabled": bool(self.settings.PROCESSING_JOBS_ENABLED),
                    "visual_search_enabled": bool(self.settings.VISUAL_SEARCH_ENABLED),
                    "backfill_enabled": bool(self.settings.VISUAL_SEARCH_BACKFILL_ENABLED),
                    "eligible_tenant_count": len(self.tenant_ids),
                    "index_configured": self.index is not None,
                },
            )
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="visual-search-backfill-scheduler",
            daemon=True,
        )
        self._thread.start()
        self.logger.info(
            "visual_backfill_scheduler_started",
            extra={"eligible_tenant_count": len(self.tenant_ids)},
        )

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(
                timeout=max(
                    1.0,
                    float(self.settings.VISUAL_SEARCH_BACKFILL_SCHEDULER_INTERVAL_SECONDS)
                    + 1.0,
                )
            )

    def tick(self) -> tuple[VisualBackfillScheduleResult, ...]:
        if not self.enabled:
            return ()
        return tuple(self._tick_tenant(tenant_id) for tenant_id in self.tenant_ids)

    def _tick_tenant(self, tenant_id: str) -> VisualBackfillScheduleResult:
        with self.session_factory() as session:
            policy = VisualBackfillPolicy.from_settings(self.settings)
            queue_depth = active_visual_queue_depth(session, tenant_id=tenant_id)
            recovery_capacity = max(0, policy.max_queued_jobs - queue_depth)
            recovered = recover_failed_visual_jobs(
                session,
                tenant_id=tenant_id,
                limit=min(policy.max_slice_assets, recovery_capacity),
            )
            if recovered:
                session.commit()

            runs = VisualSearchBackfillRunRepository(session)
            run = runs.active(tenant_id=tenant_id)
            if run is not None and run.status == "paused":
                session.rollback()
                return VisualBackfillScheduleResult(
                    tenant_id, run.id, "paused", recovered=recovered
                )

            if run is None:
                latest = runs.latest(tenant_id=tenant_id)
                if (
                    latest is not None
                    and latest.schema_version == VISUAL_EMBEDDING_SCHEMA_VERSION
                    and latest.status in {"completed", "failed", "cancelled"}
                ):
                    session.rollback()
                    return VisualBackfillScheduleResult(
                        tenant_id,
                        latest.id,
                        latest.status,
                        recovered=recovered,
                    )
                run = VisualSearchBackfillController(session).start_or_resume(
                    tenant_id=tenant_id,
                    actor_id="system:visual-backfill-scheduler",
                )

            saved, result = VisualSearchBackfillExecutor(
                session,
                ProcessingRepository(session),
                self.index,
                settings=self.settings,
                async_executor=self.async_executor,
            ).run_slice(
                tenant_id=tenant_id,
                run_id=run.id,
                max_assets=int(self.settings.VISUAL_SEARCH_BACKFILL_MAX_SLICE_ASSETS),
            )
            session.commit()
            return VisualBackfillScheduleResult(
                tenant_id,
                saved.id,
                saved.status,
                scanned=int(result.scanned) if result is not None else 0,
                enqueued=int(result.enqueued) if result is not None else 0,
                recovered=recovered,
                throttled=bool(result.throttled) if result is not None else False,
            )

    def _run(self) -> None:
        interval = max(
            1.0,
            float(self.settings.VISUAL_SEARCH_BACKFILL_SCHEDULER_INTERVAL_SECONDS),
        )
        while not self._stop.is_set():
            try:
                results = self.tick()
                for result in results:
                    if result.status == "running" or result.enqueued or result.recovered or result.throttled:
                        self.logger.info(
                            "visual_backfill_scheduler_tick",
                            extra={
                                "tenant_id": result.tenant_id,
                                "run_id": result.run_id,
                                "status": result.status,
                                "scanned": result.scanned,
                                "enqueued": result.enqueued,
                                "recovered": result.recovered,
                                "throttled": result.throttled,
                            },
                        )
            except Exception:
                self.logger.exception("visual_backfill_scheduler_tick_failed")
            self._stop.wait(interval)
