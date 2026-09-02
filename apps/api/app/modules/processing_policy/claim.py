from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, false, func, or_, select, true, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.types import JobStatus
from app.modules.ai_governance.rate_limit import AiModelRateLimitRepository, configured_model_rates
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.ai_governance.model import AiRuntimeControlModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel, TenantProviderPolicyModel
from app.modules.processing.worker_roles import (
    IMAGE_AI_JOB_TYPES, IMAGE_WORKER_JOB_TYPES,
    VIDEO_AI_JOB_TYPES, VIDEO_WORKER_JOB_TYPES,
)


AI_JOB_TYPES = ("asset_analyze", "video_analyze", "ai_batch_prepare", "ai_batch_submit", "ai_batch_poll", "ai_batch_import", "ai_batch_retry_items", "image_generate")
SOURCE_JOB_TYPES = ("source_sync", "source_asset_download")
STORAGE_JOB_TYPES = ("asset_store", "metadata_sidecar_export")
AI_MODEL_SLOT_PAYLOAD_KEY = "_ai_model_start_slot"
AI_ANALYSIS_MODEL_GATE_UNRESOLVABLE = "ai_analysis_model_gate_unresolvable"
_ANALYSIS_MODEL_GATE_UNRESOLVABLE = object()

STAGE_POLICY = {
    "source_sync": "source_sync_enabled",
    "source_asset_download": "download_enabled",
    "asset_store": "managed_storage_enabled",
    "asset_analyze": "ai_analysis_enabled",
    "video_analyze": "pipeline_enabled",
    "ai_batch_prepare": "ai_analysis_enabled",
    "ai_batch_submit": "ai_analysis_enabled",
    "ai_batch_poll": "ai_analysis_enabled",
    "ai_batch_import": "ai_analysis_enabled",
    "ai_batch_retry_items": "ai_analysis_enabled",
    "search_projection_build": "search_v2_enabled",
    "asset_index": "search_v2_enabled",
    "search_index_sync": "search_v2_enabled",
    "video_search_index": "search_v2_enabled",
    "metadata_sidecar_export": "sidecar_enabled",
    "retention_cleanup": "pipeline_enabled",
    "managed_storage_cleanup": "pipeline_enabled",
    "image_generate": "pipeline_enabled",
}


class TenantAwareJobClaimer:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def claim(self, *, worker_id: str, lease_seconds: int, now: datetime,
              allowed_job_types: tuple[str, ...], worker_role: str = "all") -> ProcessingJobModel | None:
        if not allowed_job_types:
            return None
        excluded_ai_scopes: set[tuple[str, str | None]] = set()
        while True:
            eligibility = self._eligibility(
                now, allowed_job_types, excluded_ai_scopes=excluded_ai_scopes,
                worker_role=worker_role,
            )
            statement = (
                select(ProcessingJobModel)
                .where(eligibility)
                .order_by(
                    ProcessingJobModel.priority.desc(),
                    ProcessingJobModel.next_attempt_at,
                    ProcessingJobModel.created_at,
                )
                .limit(1)
            )
            if self.session.get_bind().dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            candidate = self.session.scalar(statement)
            if candidate is None:
                return None

            already_accounted = candidate.concurrency_accounted
            if not already_accounted and not self._reserve(candidate):
                return None

            model_slot = None
            if candidate.job_type == "asset_analyze":
                model_slot = self._reserve_analysis_model(candidate, now)
                if model_slot is _ANALYSIS_MODEL_GATE_UNRESOLVABLE:
                    if candidate.concurrency_accounted:
                        self.release(candidate)
                    self._terminalize_unresolvable_analysis(candidate, now)
                    continue
                if model_slot is None:
                    if not already_accounted:
                        self.release(candidate)
                    excluded_ai_scopes.add(
                        (candidate.tenant_id, candidate.provider_key)
                    )
                    continue

            lease_expires_at = now + timedelta(seconds=lease_seconds)
            values = {
                "status": JobStatus.PROCESSING.value,
                "claimed_by": worker_id,
                "claimed_at": now,
                "lease_expires_at": lease_expires_at,
                "attempt_count": ProcessingJobModel.attempt_count + 1,
                "concurrency_accounted": True,
                "updated_at": now,
            }
            if model_slot is not None:
                payload = dict(candidate.payload_json or {})
                payload[AI_MODEL_SLOT_PAYLOAD_KEY] = {
                    "provider": model_slot["provider"],
                    "model": model_slot["model"],
                    "reserved_at": now.isoformat(),
                    "next_eligible_at": model_slot["next_eligible_at"].isoformat(),
                    "attempt_count": candidate.attempt_count + 1,
                    "worker_id": worker_id,
                }
                values["payload_json"] = payload
            claimed = self.session.scalars(
                update(ProcessingJobModel)
                .where(ProcessingJobModel.id == candidate.id, self._base_eligibility(now))
                .values(**values)
                .returning(ProcessingJobModel)
                .execution_options(
                    synchronize_session=False, populate_existing=True
                )
            ).first()
            if claimed is None and not already_accounted:
                self.release(candidate)
            return claimed

    def release(self, job: ProcessingJobModel) -> None:
        if not job.concurrency_accounted:
            return
        category = self._category(job.job_type)
        values = {
            "total_active_jobs": TenantProcessingPolicyModel.total_active_jobs - 1,
            "updated_at": datetime.now(timezone.utc),
        }
        if category:
            column = getattr(TenantProcessingPolicyModel, f"{category}_active_jobs")
            values[f"{category}_active_jobs"] = column - 1
        self.session.execute(
            update(TenantProcessingPolicyModel)
            .where(TenantProcessingPolicyModel.tenant_id == job.tenant_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if job.provider_key and job.provider_scope:
            self.session.execute(
                update(TenantProviderPolicyModel)
                .where(
                    TenantProviderPolicyModel.tenant_id == job.tenant_id,
                    TenantProviderPolicyModel.provider_key == job.provider_key,
                    TenantProviderPolicyModel.provider_scope == job.provider_scope,
                )
                .values(**self._provider_release_values(job))
                .execution_options(synchronize_session=False)
            )
        job.concurrency_accounted = False
        self.session.flush()

    def release_exhausted(self, now: datetime) -> None:
        jobs = list(self.session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.status == JobStatus.PROCESSING.value,
            ProcessingJobModel.lease_expires_at <= now,
            ProcessingJobModel.attempt_count >= ProcessingJobModel.max_attempts,
            ProcessingJobModel.concurrency_accounted.is_(True),
        )))
        for job in jobs:
            self.release(job)

    def _reserve(self, job: ProcessingJobModel) -> bool:
        category = self._category(job.job_type)
        conditions = [
            TenantProcessingPolicyModel.tenant_id == job.tenant_id,
            TenantProcessingPolicyModel.pipeline_enabled.is_(True),
            TenantProcessingPolicyModel.processing_paused.is_(False),
            TenantProcessingPolicyModel.total_active_jobs < TenantProcessingPolicyModel.total_active_jobs_limit,
        ]
        values = {
            "total_active_jobs": TenantProcessingPolicyModel.total_active_jobs + 1,
            "updated_at": datetime.now(timezone.utc),
        }
        if category:
            active = getattr(TenantProcessingPolicyModel, f"{category}_active_jobs")
            limit = getattr(TenantProcessingPolicyModel, f"{category}_active_jobs_limit")
            conditions.append(active < limit)
            values[f"{category}_active_jobs"] = active + 1
        tenant_reserved = self.session.scalar(
            update(TenantProcessingPolicyModel)
            .where(*conditions)
            .values(**values)
            .returning(TenantProcessingPolicyModel.tenant_id)
            .execution_options(synchronize_session=False)
        )
        if tenant_reserved is None:
            return False
        if job.provider_key and job.provider_scope:
            provider = self.session.scalar(select(TenantProviderPolicyModel).where(
                TenantProviderPolicyModel.tenant_id == job.tenant_id,
                TenantProviderPolicyModel.provider_key == job.provider_key,
                TenantProviderPolicyModel.provider_scope == job.provider_scope,
            ))
            if provider is not None:
                reserved = self.session.scalar(
                    update(TenantProviderPolicyModel)
                    .where(
                        TenantProviderPolicyModel.id == provider.id,
                        TenantProviderPolicyModel.processing_enabled.is_(True),
                        TenantProviderPolicyModel.processing_paused.is_(False),
                        TenantProviderPolicyModel.active_jobs < TenantProviderPolicyModel.active_jobs_limit,
                        *self._provider_reserve_conditions(job),
                    )
                    .values(**self._provider_reserve_values(job))
                    .returning(TenantProviderPolicyModel.id)
                    .execution_options(synchronize_session=False)
                )
                if reserved is None:
                    job.concurrency_accounted = True
                    self.release(job)
                    return False
        job.concurrency_accounted = True
        self.session.flush()
        return True

    def _eligibility(
        self, now: datetime, allowed_job_types: tuple[str, ...],
        *, excluded_ai_scopes: set[tuple[str, str | None]] | None = None,
        worker_role: str = "all",
    ):
        policy_conditions = [
            TenantProcessingPolicyModel.tenant_id == ProcessingJobModel.tenant_id,
            TenantProcessingPolicyModel.pipeline_enabled.is_(True),
            TenantProcessingPolicyModel.processing_paused.is_(False),
            or_(
                ProcessingJobModel.concurrency_accounted.is_(True),
                TenantProcessingPolicyModel.total_active_jobs < TenantProcessingPolicyModel.total_active_jobs_limit,
            ),
            self._stage_enabled(),
            self._category_limit_available(),
        ]
        provider_blocked = exists(select(TenantProviderPolicyModel.id).where(
            TenantProviderPolicyModel.tenant_id == ProcessingJobModel.tenant_id,
            TenantProviderPolicyModel.provider_key == ProcessingJobModel.provider_key,
            TenantProviderPolicyModel.provider_scope == ProcessingJobModel.provider_scope,
            or_(
                TenantProviderPolicyModel.processing_enabled.is_(False),
                TenantProviderPolicyModel.processing_paused.is_(True),
                TenantProviderPolicyModel.emergency_stop.is_(True),
                and_(ProcessingJobModel.job_type == "asset_analyze", TenantProviderPolicyModel.single_enabled.is_(False)),
                and_(ProcessingJobModel.job_type.like("ai_batch_%"), TenantProviderPolicyModel.batch_enabled.is_(False)),
                and_(ProcessingJobModel.concurrency_accounted.is_(False), ProcessingJobModel.job_type == "asset_analyze", TenantProviderPolicyModel.single_active_jobs >= TenantProviderPolicyModel.single_active_jobs_limit),
                and_(ProcessingJobModel.concurrency_accounted.is_(False), ProcessingJobModel.job_type == "ai_batch_submit", TenantProviderPolicyModel.batch_active_jobs >= TenantProviderPolicyModel.batch_active_jobs_limit),
                and_(
                    ProcessingJobModel.concurrency_accounted.is_(False),
                    TenantProviderPolicyModel.active_jobs >= TenantProviderPolicyModel.active_jobs_limit,
                ),
            ),
        ))
        excluded_ai_scopes = excluded_ai_scopes or set()
        scope_available = and_(
            true(),
            *[
                ~and_(
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.provider_key == provider_key,
                )
                for tenant_id, provider_key in excluded_ai_scopes
            ],
        )
        return and_(
            self._base_eligibility(now),
            scope_available,
            ProcessingJobModel.job_type.in_(allowed_job_types),
            self._worker_role_available(worker_role),
            self._runtime_control_available(),
            exists(select(TenantProcessingPolicyModel.tenant_id).where(*policy_conditions)),
            or_(ProcessingJobModel.provider_key.is_(None), ~provider_blocked),
        )

    @staticmethod
    def _worker_role_available(worker_role: str):
        role = worker_role.strip().casefold()
        if role == "all":
            return true()
        image_enabled = exists(
            select(TenantProcessingPolicyModel.tenant_id)
            .where(
                TenantProcessingPolicyModel.tenant_id == ProcessingJobModel.tenant_id,
                TenantProcessingPolicyModel.ai_analysis_enabled.is_(True),
            )
            .correlate_except(TenantProcessingPolicyModel)
        )
        video_paused = exists(
            select(TenantProviderPolicyModel.id)
            .where(
                TenantProviderPolicyModel.tenant_id == ProcessingJobModel.tenant_id,
                TenantProviderPolicyModel.provider_key == "gemini",
                TenantProviderPolicyModel.provider_scope == "video",
                or_(
                    TenantProviderPolicyModel.processing_enabled.is_(False),
                    TenantProviderPolicyModel.processing_paused.is_(True),
                    TenantProviderPolicyModel.emergency_stop.is_(True),
                ),
            )
            .correlate_except(TenantProviderPolicyModel)
        )
        if role == "image":
            return or_(
                ProcessingJobModel.job_type.in_(IMAGE_WORKER_JOB_TYPES),
                and_(
                    ProcessingJobModel.job_type.in_(VIDEO_AI_JOB_TYPES),
                    ~image_enabled,
                    ~video_paused,
                ),
            )
        if role == "video":
            return or_(
                ProcessingJobModel.job_type.in_(VIDEO_WORKER_JOB_TYPES),
                and_(
                    ProcessingJobModel.job_type.in_(IMAGE_AI_JOB_TYPES),
                    image_enabled,
                    video_paused,
                ),
            )
        return false()

    @staticmethod
    def _base_eligibility(now: datetime):
        return and_(
            ProcessingJobModel.cancellation_requested.is_(False),
            ProcessingJobModel.attempt_count < ProcessingJobModel.max_attempts,
            or_(
                and_(
                    ProcessingJobModel.status.in_(
                        (JobStatus.PENDING.value, JobStatus.RETRY.value)
                    ),
                    or_(
                        ProcessingJobModel.next_attempt_at.is_(None),
                        ProcessingJobModel.next_attempt_at <= now,
                    ),
                ),
                and_(ProcessingJobModel.status == JobStatus.PROCESSING.value, ProcessingJobModel.lease_expires_at <= now),
            ),
        )

    @staticmethod
    def _stage_enabled():
        clauses = []
        for job_type, field in STAGE_POLICY.items():
            clauses.append(and_(
                ProcessingJobModel.job_type == job_type,
                getattr(TenantProcessingPolicyModel, field).is_(True),
            ))
        return or_(*clauses)

    @staticmethod
    def _category_limit_available():
        return or_(
            ProcessingJobModel.concurrency_accounted.is_(True),
            and_(ProcessingJobModel.job_type.in_(AI_JOB_TYPES), TenantProcessingPolicyModel.ai_active_jobs < TenantProcessingPolicyModel.ai_active_jobs_limit),
            and_(ProcessingJobModel.job_type.in_(SOURCE_JOB_TYPES), TenantProcessingPolicyModel.source_active_jobs < TenantProcessingPolicyModel.source_active_jobs_limit),
            and_(ProcessingJobModel.job_type.in_(STORAGE_JOB_TYPES), TenantProcessingPolicyModel.storage_active_jobs < TenantProcessingPolicyModel.storage_active_jobs_limit),
            ~ProcessingJobModel.job_type.in_(AI_JOB_TYPES + SOURCE_JOB_TYPES + STORAGE_JOB_TYPES),
        )

    @staticmethod
    def _category(job_type: str) -> str | None:
        if job_type in AI_JOB_TYPES:
            return "ai"
        if job_type in SOURCE_JOB_TYPES:
            return "source"
        if job_type in STORAGE_JOB_TYPES:
            return "storage"
        return None

    @staticmethod
    def _provider_reserve_conditions(job):
        if job.provider_scope != "ai":
            return []
        if job.job_type == "asset_analyze":
            return [
                TenantProviderPolicyModel.single_enabled.is_(True),
                TenantProviderPolicyModel.emergency_stop.is_(False),
                TenantProviderPolicyModel.single_active_jobs < TenantProviderPolicyModel.single_active_jobs_limit,
            ]
        if job.job_type == "ai_batch_submit":
            return [TenantProviderPolicyModel.batch_enabled.is_(True), TenantProviderPolicyModel.emergency_stop.is_(False), TenantProviderPolicyModel.batch_active_jobs < TenantProviderPolicyModel.batch_active_jobs_limit]
        if job.job_type.startswith("ai_batch_"):
            return [TenantProviderPolicyModel.batch_enabled.is_(True), TenantProviderPolicyModel.emergency_stop.is_(False)]
        return [TenantProviderPolicyModel.emergency_stop.is_(False)]

    @staticmethod
    def _provider_reserve_values(job):
        values = {"active_jobs": TenantProviderPolicyModel.active_jobs + 1}
        if job.provider_scope == "ai" and job.job_type == "asset_analyze":
            values["single_active_jobs"] = TenantProviderPolicyModel.single_active_jobs + 1
        elif job.provider_scope == "ai" and job.job_type == "ai_batch_submit":
            values["batch_active_jobs"] = TenantProviderPolicyModel.batch_active_jobs + 1
        return values

    @staticmethod
    def _provider_release_values(job):
        values = {"active_jobs": TenantProviderPolicyModel.active_jobs - 1}
        if job.provider_scope == "ai" and job.job_type == "asset_analyze":
            values["single_active_jobs"] = TenantProviderPolicyModel.single_active_jobs - 1
        elif job.provider_scope == "ai" and job.job_type == "ai_batch_submit":
            values["batch_active_jobs"] = TenantProviderPolicyModel.batch_active_jobs - 1
        return values

    @staticmethod
    def _runtime_control_available():
        stopped = exists(select(AiRuntimeControlModel.control_key).where(
            AiRuntimeControlModel.stopped.is_(True),
            or_(
                AiRuntimeControlModel.control_key == "global",
                AiRuntimeControlModel.control_key == ProcessingJobModel.provider_key,
            ),
        ))
        return or_(~ProcessingJobModel.job_type.in_(AI_JOB_TYPES), ~stopped)

    def _reserve_analysis_model(
        self, job: ProcessingJobModel, now: datetime
    ) -> dict[str, object] | object | None:
        analysis_id = self._analysis_id_for_model_gate(job)
        if analysis_id is None:
            return _ANALYSIS_MODEL_GATE_UNRESOLVABLE
        analysis = self.session.scalar(
            select(AssetAiAnalysisModel).where(
                AssetAiAnalysisModel.id == analysis_id,
                AssetAiAnalysisModel.tenant_id == job.tenant_id,
            )
        )
        if analysis is None:
            return _ANALYSIS_MODEL_GATE_UNRESOLVABLE
        provider = (
            analysis.ai_provider.strip()
            if isinstance(analysis.ai_provider, str)
            else ""
        )
        if not provider:
            return _ANALYSIS_MODEL_GATE_UNRESOLVABLE
        # Reused completed analyses do not start a provider request.
        if analysis.status == "completed":
            model = analysis.ai_model.strip() if isinstance(analysis.ai_model, str) else ""
            if not model:
                return _ANALYSIS_MODEL_GATE_UNRESOLVABLE
            return {"provider": provider, "model": model, "next_eligible_at": now}
        model_rates = configured_model_rates(
            self.settings, provider, analysis.ai_model
        )
        if not model_rates:
            model = (
                analysis.ai_model.strip()
                if isinstance(analysis.ai_model, str)
                else ""
            )
            if not model:
                return _ANALYSIS_MODEL_GATE_UNRESOLVABLE
            return {
                "provider": provider,
                "model": model,
                "next_eligible_at": now,
            }
        limiter = AiModelRateLimitRepository(self.session)
        for model, rpm in model_rates:
            decision = limiter.reserve_start(
                tenant_id=job.tenant_id,
                provider=provider,
                model=model,
                rpm=rpm,
                minimum_interval_seconds=self.settings.AI_JOB_MIN_INTERVAL_SECONDS,
                now=now,
            )
            if decision.allowed:
                return {
                    "provider": provider,
                    "model": model,
                    "next_eligible_at": decision.next_eligible_at,
                }
        return None

    @staticmethod
    def _analysis_id_for_model_gate(job: ProcessingJobModel) -> str | None:
        payload_analysis_id = (job.payload_json or {}).get("analysis_id")
        if isinstance(payload_analysis_id, str) and payload_analysis_id.strip():
            return payload_analysis_id.strip()
        if job.entity_type in {"asset_ai_analysis", "ai_analysis"}:
            entity_id = job.entity_id
            if isinstance(entity_id, str) and entity_id.strip():
                return entity_id.strip()
        return None

    def _terminalize_unresolvable_analysis(
        self, job: ProcessingJobModel, now: datetime
    ) -> None:
        job.status = JobStatus.FAILED.value
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.next_attempt_at = now
        job.completed_at = now
        job.last_error_code = AI_ANALYSIS_MODEL_GATE_UNRESOLVABLE
        job.last_error_message = (
            "AI analysis reference could not be resolved for this tenant."
        )
        job.updated_at = now
        self.session.flush()
