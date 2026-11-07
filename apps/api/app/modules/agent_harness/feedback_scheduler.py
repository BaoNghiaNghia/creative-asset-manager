"""Fail-closed, zero-provider periodic Stage 1 evidence outbox drain.

Runs only on CAM's existing operational-scheduler worker(s). A second worker
cannot process a PostgreSQL event concurrently (SKIP LOCKED). Each tick is
bounded and committed atomically with candidate changes. Not a new job queue.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings
from .feedback_outbox import DrainResult, drain_feedback_events


class AgentFeedbackOutboxScheduler:
    INTERVAL_SECONDS = 120

    def __init__(
        self, session_factory: Callable[[], Session], settings: Settings, *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.PROCESSING_JOBS_ENABLED
            and self.settings.AGENT_HARNESS_FEEDBACK_SYNC_ENABLED
            and self.settings.AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED
            and self.settings.AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED
            and not self.settings.AI_EMERGENCY_STOP_ENABLED
        )

    def tick(self) -> DrainResult:
        if not self.enabled:
            return DrainResult()
        with self.session_factory() as session:
            result = drain_feedback_events(session)
            session.commit()
        if result.processed_events:
            self.logger.info(
                "agent_feedback_outbox_tick",
                extra={
                    "processed_events": result.processed_events,
                    "created_candidates": result.candidates_created,
                    "revoked_candidates": result.candidates_revoked,
                    "cohorts": result.cohorts_reconciled,
                    "unconfigured_cohorts": result.unconfigured_cohorts,
                },
            )
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # Keep the outbox durable; do not touch production generation
                # jobs when evidence reconciliation is temporarily unavailable.
                self.logger.exception("agent_feedback_outbox_tick_failed")
            self._stop.wait(self.INTERVAL_SECONDS)

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="agent-feedback-outbox-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout if timeout is not None else self.INTERVAL_SECONDS + 1)
