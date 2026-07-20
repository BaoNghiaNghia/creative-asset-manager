from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.domain.processing.types import JobStatus
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel, TenantProviderPolicyModel


AI_JOB_TYPES = ("asset_analyze",)
SOURCE_JOB_TYPES = ("source_sync", "source_asset_download")
STORAGE_JOB_TYPES = ("asset_store", "metadata_sidecar_export")

STAGE_POLICY = {
    "source_sync": "source_sync_enabled",
    "source_asset_download": "download_enabled",
    "asset_store": "managed_storage_enabled",
    "asset_analyze": "ai_analysis_enabled",
    "search_projection_build": "search_v2_enabled",
    "asset_index": "search_v2_enabled",
    "metadata_sidecar_export": "sidecar_enabled",
}


class TenantAwareJobClaimer:
    def __init__(self, session: Session):
        self.session = session

    def claim(self, *, worker_id: str, lease_seconds: int, now: datetime,
              allowed_job_types: tuple[str, ...]) -> ProcessingJobModel | None:
        if not allowed_job_types:
            return None
        eligibility = self._eligibility(now, allowed_job_types)
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

        lease_expires_at = now + timedelta(seconds=lease_seconds)
        claimed = self.session.scalars(
            update(ProcessingJobModel)
            .where(ProcessingJobModel.id == candidate.id, self._base_eligibility(now))
            .values(
                status=JobStatus.PROCESSING.value,
                claimed_by=worker_id,
                claimed_at=now,
                lease_expires_at=lease_expires_at,
                attempt_count=ProcessingJobModel.attempt_count + 1,
                concurrency_accounted=True,
                updated_at=now,
            )
            .returning(ProcessingJobModel)
            .execution_options(synchronize_session=False, populate_existing=True)
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
                .values(active_jobs=TenantProviderPolicyModel.active_jobs - 1)
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
                    )
                    .values(active_jobs=TenantProviderPolicyModel.active_jobs + 1)
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

    def _eligibility(self, now: datetime, allowed_job_types: tuple[str, ...]):
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
                and_(
                    ProcessingJobModel.concurrency_accounted.is_(False),
                    TenantProviderPolicyModel.active_jobs >= TenantProviderPolicyModel.active_jobs_limit,
                ),
            ),
        ))
        return and_(
            self._base_eligibility(now),
            ProcessingJobModel.job_type.in_(allowed_job_types),
            exists(select(TenantProcessingPolicyModel.tenant_id).where(*policy_conditions)),
            or_(ProcessingJobModel.provider_key.is_(None), ~provider_blocked),
        )

    @staticmethod
    def _base_eligibility(now: datetime):
        return and_(
            ProcessingJobModel.attempt_count < ProcessingJobModel.max_attempts,
            or_(
                and_(ProcessingJobModel.status.in_((JobStatus.PENDING.value, JobStatus.RETRY.value)), ProcessingJobModel.next_attempt_at <= now),
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
