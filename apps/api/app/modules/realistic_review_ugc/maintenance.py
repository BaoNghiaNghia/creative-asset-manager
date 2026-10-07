from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_governance.gemini_quota import GeminiProjectQuotaRepository
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcScoutAgentModel,
)
from app.modules.realistic_review_ugc.scout_automation import SCOUT_OFFLINE_SECONDS
from app.modules.realistic_review_ugc.service import (
    ANALYZE_JOB_TYPE,
    IMPORT_JOB_TYPE,
    RrugcService,
)

_ACTIVE_JOB_STATUSES = ("pending", "retry", "processing")
_GEMINI_DEFER_CODE = "gemini_model_pool_temporarily_unavailable"
_MIN_SCOUT_VERSION = 44
_VERSION_RE = re.compile(r"(\d+)$")


@dataclass(frozen=True, slots=True)
class RrugcHealthSnapshot:
    orphan_analysis_queued: int
    stale_importing: int
    gemini_deferred: int
    oldest_analysis_queue_age_seconds: int | None
    scout_total: int
    scout_online: int
    scout_offline: int
    scout_outdated: int
    gemini_capacity_available: bool
    checked_at: datetime


class RrugcMaintenanceService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    @staticmethod
    def _active_job_exists(job_type: str):
        return exists(
            select(ProcessingJobModel.id).where(
                ProcessingJobModel.tenant_id == RrugcCandidateModel.tenant_id,
                ProcessingJobModel.entity_type == "rrugc_candidate",
                ProcessingJobModel.entity_id == RrugcCandidateModel.id,
                ProcessingJobModel.job_type == job_type,
                ProcessingJobModel.status.in_(_ACTIVE_JOB_STATUSES),
            )
        )

    def _orphan_analysis_query(self):
        return (
            select(RrugcCandidateModel)
            .where(
                RrugcCandidateModel.status == "analysis_queued",
                ~self._active_job_exists(ANALYZE_JOB_TYPE),
            )
            .order_by(RrugcCandidateModel.updated_at.asc(), RrugcCandidateModel.id.asc())
        )

    def _stale_import_query(self, now: datetime):
        cutoff = now - timedelta(seconds=self.settings.RRUGC_IMPORT_STALE_SECONDS)
        return (
            select(RrugcCandidateModel)
            .where(
                RrugcCandidateModel.status == "importing",
                RrugcCandidateModel.updated_at <= cutoff,
                ~self._active_job_exists(IMPORT_JOB_TYPE),
            )
            .order_by(RrugcCandidateModel.updated_at.asc(), RrugcCandidateModel.id.asc())
        )

    @staticmethod
    def _scout_version(value: str | None) -> int:
        match = _VERSION_RE.search(str(value or "").strip())
        return int(match.group(1)) if match else 0

    def _credential_fingerprints(self, tenant_id: str) -> tuple[str, ...]:
        repository = CreativeAiCredentialRepository(self.session, None)
        values: list[str] = []
        primary = repository.get_metadata(tenant_id, provider="gemini")
        if primary is not None and primary.status == "active":
            values.append(primary.secret_fingerprint)
        else:
            fallback = (self.settings.GEMINI_API_KEY or "").strip()
            if fallback:
                values.append(hashlib.sha256(fallback.encode()).hexdigest())
        values.extend(
            item.secret_fingerprint
            for item in repository.list_backup_metadata(tenant_id)
            if item.status == "active"
        )
        return tuple(dict.fromkeys(values))

    def gemini_capacity_available(self, tenant_id: str, *, now: datetime) -> bool:
        fingerprints = self._credential_fingerprints(tenant_id)
        if not fingerprints:
            return False
        quota = GeminiProjectQuotaRepository(self.session)
        for model, limit in self.settings.gemini_model_limits.items():
            for fingerprint in fingerprints:
                decision = quota.check_request_availability(
                    quota_scope=(
                        f"{self.settings.GEMINI_PROJECT_QUOTA_SCOPE}:"
                        f"{tenant_id}:{fingerprint}"
                    ),
                    model=model,
                    rpd=limit.rpd,
                    project_rpd=self.settings.gemini_project_daily_request_limit,
                    now=now,
                )
                if decision.allowed:
                    return True
        return False

    def health(self, tenant_id: str, *, now: datetime | None = None) -> RrugcHealthSnapshot:
        current = now or datetime.now(timezone.utc)
        orphan_count = int(
            self.session.scalar(
                select(func.count()).select_from(
                    self._orphan_analysis_query()
                    .where(RrugcCandidateModel.tenant_id == tenant_id)
                    .subquery()
                )
            ) or 0
        )
        stale_import_count = int(
            self.session.scalar(
                select(func.count()).select_from(
                    self._stale_import_query(current)
                    .where(RrugcCandidateModel.tenant_id == tenant_id)
                    .subquery()
                )
            ) or 0
        )
        deferred_query = select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == ANALYZE_JOB_TYPE,
            ProcessingJobModel.status.in_(("pending", "retry")),
            ProcessingJobModel.last_error_code == _GEMINI_DEFER_CODE,
            ProcessingJobModel.next_attempt_at > current,
        )
        gemini_deferred = int(
            self.session.scalar(select(func.count()).select_from(deferred_query.subquery())) or 0
        )
        oldest = self.session.scalar(
            select(func.min(ProcessingJobModel.created_at)).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == ANALYZE_JOB_TYPE,
                ProcessingJobModel.status.in_(("pending", "retry")),
            )
        )
        oldest_age = None
        if oldest is not None:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            oldest_age = max(0, int((current - oldest).total_seconds()))

        agents = list(
            self.session.scalars(
                select(RrugcScoutAgentModel).where(
                    RrugcScoutAgentModel.tenant_id == tenant_id,
                    RrugcScoutAgentModel.active.is_(True),
                )
            )
        )
        online = 0
        outdated = 0
        for agent in agents:
            seen = agent.last_seen_at
            if seen is not None and seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            is_online = bool(
                seen is not None
                and current - seen <= timedelta(seconds=SCOUT_OFFLINE_SECONDS)
            )
            if is_online:
                online += 1
                if self._scout_version(agent.client_version) < _MIN_SCOUT_VERSION:
                    outdated += 1

        return RrugcHealthSnapshot(
            orphan_analysis_queued=orphan_count,
            stale_importing=stale_import_count,
            gemini_deferred=gemini_deferred,
            oldest_analysis_queue_age_seconds=oldest_age,
            scout_total=len(agents),
            scout_online=online,
            scout_offline=max(0, len(agents) - online),
            scout_outdated=outdated,
            gemini_capacity_available=self.gemini_capacity_available(tenant_id, now=current),
            checked_at=current,
        )

    def repair_tenant(self, tenant_id: str, *, now: datetime | None = None) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        service = RrugcService(self.session)
        repaired_analysis = 0
        repaired_imports = 0
        woken_gemini = 0
        scouts_offlined = 0

        orphan_rows = list(
            self.session.scalars(
                self._orphan_analysis_query()
                .where(RrugcCandidateModel.tenant_id == tenant_id)
                .limit(self.settings.RRUGC_MAINTENANCE_BATCH_SIZE)
            )
        )
        for candidate in orphan_rows:
            service.enqueue_analysis(candidate, increment_revision=True)
            candidate.last_error_code = "rrugc_analysis_watchdog_requeued"
            repaired_analysis += 1

        stale_import_rows = list(
            self.session.scalars(
                self._stale_import_query(current)
                .where(RrugcCandidateModel.tenant_id == tenant_id)
                .limit(self.settings.RRUGC_IMPORT_RECOVERY_BATCH_SIZE)
            )
        )
        for candidate in stale_import_rows:
            candidate.status = "import_failed"
            service.enqueue_import(candidate, retry=True)
            candidate.last_error_code = "rrugc_import_watchdog_requeued"
            repaired_imports += 1

        if self.gemini_capacity_available(tenant_id, now=current):
            deferred_rows = list(
                self.session.scalars(
                    select(ProcessingJobModel)
                    .where(
                        ProcessingJobModel.tenant_id == tenant_id,
                        ProcessingJobModel.job_type == ANALYZE_JOB_TYPE,
                        ProcessingJobModel.status.in_(("pending", "retry")),
                        ProcessingJobModel.last_error_code == _GEMINI_DEFER_CODE,
                        ProcessingJobModel.next_attempt_at > current,
                    )
                    .order_by(
                        ProcessingJobModel.updated_at.asc(),
                        ProcessingJobModel.created_at.asc(),
                    )
                    .limit(self.settings.RRUGC_GEMINI_RETRY_WAKE_BATCH_SIZE)
                )
            )
            for job in deferred_rows:
                job.next_attempt_at = current
                job.updated_at = current
                woken_gemini += 1

        offline_cutoff = current - timedelta(seconds=SCOUT_OFFLINE_SECONDS)
        stale_agents = list(
            self.session.scalars(
                select(RrugcScoutAgentModel).where(
                    RrugcScoutAgentModel.tenant_id == tenant_id,
                    RrugcScoutAgentModel.active.is_(True),
                    RrugcScoutAgentModel.last_seen_at.is_not(None),
                    RrugcScoutAgentModel.last_seen_at < offline_cutoff,
                    RrugcScoutAgentModel.status != "offline",
                )
            )
        )
        for agent in stale_agents:
            agent.status = "offline"
            scouts_offlined += 1

        self.session.commit()
        return {
            "analysis_requeued": repaired_analysis,
            "imports_requeued": repaired_imports,
            "gemini_jobs_woken": woken_gemini,
            "scouts_offlined": scouts_offlined,
        }


class RrugcMaintenanceScheduler:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        *,
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
            and self.settings.RRUGC_MAINTENANCE_ENABLED
        )

    @property
    def interval_seconds(self) -> int:
        return max(30, int(self.settings.RRUGC_MAINTENANCE_INTERVAL_SECONDS))

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="rrugc-maintenance-scheduler",
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
                self.logger.exception("rrugc_maintenance_tick_failed")
            self._stop.wait(self.interval_seconds)

    def _tenant_ids(self) -> tuple[str, ...]:
        with self.session_factory() as session:
            ids = set(
                session.scalars(select(RrugcCampaignModel.tenant_id).distinct())
            )
            ids.update(
                session.scalars(select(RrugcScoutAgentModel.tenant_id).distinct())
            )
            return tuple(sorted(item for item in ids if item))

    def tick(self) -> dict[str, dict[str, int]]:
        if not self.enabled:
            return {}
        results: dict[str, dict[str, int]] = {}
        for tenant_id in self._tenant_ids():
            with self.session_factory() as session:
                counts = RrugcMaintenanceService(session, self.settings).repair_tenant(
                    tenant_id
                )
            results[tenant_id] = counts
            if any(counts.values()):
                self.logger.info(
                    "rrugc_maintenance_repaired",
                    extra={"tenant_id": tenant_id, **counts},
                )
        return results
