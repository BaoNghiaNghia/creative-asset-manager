from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_batch.model import AiBatchItemModel, AiBatchJobModel
from app.modules.ai_governance.model import (
    AiBudgetAccountModel,
    TenantAiBudgetPolicyModel,
)
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.request_model import (
    AiAnalysisRequestItemModel,
    AiAnalysisRequestModel,
)
from app.modules.ai_metadata.schema import BulkAssetAnalysisRequest
from app.modules.ai_metadata.selection import AiSelection, AiSelectionError
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository


class AnalysisRequestIdempotencyConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BulkCreateResult:
    request: AiAnalysisRequestModel
    items: tuple[AiAnalysisRequestItemModel, ...]
    reused: bool


def canonical_bulk_request(
    body: BulkAssetAnalysisRequest,
) -> tuple[dict[str, Any], bytes, str]:
    document = body.model_dump(mode="json")
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return document, encoded, hashlib.sha256(encoded).hexdigest()


class AiAnalysisRequestRepository:
    def __init__(self, session: Session):
        self.session = session

    def by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> AiAnalysisRequestModel | None:
        return self.session.scalar(
            select(AiAnalysisRequestModel).where(
                AiAnalysisRequestModel.tenant_id == tenant_id,
                AiAnalysisRequestModel.idempotency_key == idempotency_key,
            )
        )

    def get(self, tenant_id: str, request_id: str) -> AiAnalysisRequestModel:
        value = self.session.scalar(
            select(AiAnalysisRequestModel).where(
                AiAnalysisRequestModel.tenant_id == tenant_id,
                AiAnalysisRequestModel.id == request_id,
            )
        )
        if value is None:
            raise LookupError(request_id)
        return value

    def items(
        self, tenant_id: str, request_id: str
    ) -> list[AiAnalysisRequestItemModel]:
        return list(self.session.scalars(
            select(AiAnalysisRequestItemModel).where(
                AiAnalysisRequestItemModel.tenant_id == tenant_id,
                AiAnalysisRequestItemModel.request_id == request_id,
            ).order_by(AiAnalysisRequestItemModel.created_at,
                       AiAnalysisRequestItemModel.id)
        ))

    def batch_ids(self, tenant_id: str, request_id: str) -> list[str]:
        return list(self.session.scalars(
            select(AiBatchItemModel.batch_job_id)
            .join(
                AiAnalysisRequestItemModel,
                AiAnalysisRequestItemModel.analysis_id
                == AiBatchItemModel.analysis_id,
            )
            .where(
                AiAnalysisRequestItemModel.tenant_id == tenant_id,
                AiAnalysisRequestItemModel.request_id == request_id,
                AiBatchItemModel.tenant_id == tenant_id,
            )
            .distinct()
            .order_by(AiBatchItemModel.batch_job_id)
        ))


class AiAnalysisOrchestrationService:
    """Persist analysis intent and enqueue only provider-neutral worker jobs."""

    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.requests = AiAnalysisRequestRepository(session)
        self.metadata = AiMetadataRepository(session)
        self.processing = ProcessingRepository(session)
        self.governance = AiGovernanceRepository(session)

    def create_bulk(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        idempotency_key: str,
        body: BulkAssetAnalysisRequest,
        selection: AiSelection | None,
        selection_error: AiSelectionError | None = None,
    ) -> BulkCreateResult:
        document, encoded, request_hash = canonical_bulk_request(body)
        if len(encoded) > self.settings.AI_ANALYSIS_BULK_MAX_PAYLOAD_BYTES:
            raise ValueError("canonical request exceeds the bulk payload limit")
        existing = self.requests.by_idempotency(tenant_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AnalysisRequestIdempotencyConflict(idempotency_key)
            return BulkCreateResult(
                existing,
                tuple(self.requests.items(tenant_id, existing.id)),
                True,
            )

        profile = self.metadata.find_active_profile(
            tenant_id=tenant_id,
            profile_name=body.metadata_profile,
            profile_version=body.metadata_profile_version,
        )
        if profile is None:
            raise LookupError("metadata_profile")
        model = (
            selection.model
            if selection is not None
            else body.ai_model or self._default_model(body.ai_provider)
        )
        request = AiAnalysisRequestModel(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_json=document,
            metadata_profile_id=profile.id,
            metadata_profile=profile.profile_name,
            metadata_profile_version=profile.profile_version,
            ai_provider=body.ai_provider,
            ai_model=model,
            processing_mode=body.processing_mode,
            item_count=len(body.asset_ids),
            warning=(
                "Batch processing can have delayed completion."
                if body.processing_mode == "batch" and len(body.asset_ids) == 1
                else None
            ),
            created_by=actor_id,
        )
        try:
            with self.session.begin_nested():
                self.session.add(request)
                self.session.flush()
        except IntegrityError:
            existing = self.requests.by_idempotency(tenant_id, idempotency_key)
            if existing is None:
                raise
            if existing.request_hash != request_hash:
                raise AnalysisRequestIdempotencyConflict(idempotency_key)
            return BulkCreateResult(
                existing,
                tuple(self.requests.items(tenant_id, existing.id)),
                True,
            )

        created_items: list[AiAnalysisRequestItemModel] = []
        batch_analyses: list[AssetAiAnalysisModel] = []
        batch_items: list[AiAnalysisRequestItemModel] = []
        planned_cost = 0
        pipeline_version = (
            "single-asset-v1"
            if body.processing_mode == "single"
            else "batch-asset-v1"
        )
        prompt_version = f"profile-{profile.profile_version}"

        for asset_id in body.asset_ids:
            asset = self.session.get(AssetModel, asset_id)
            if asset is None:
                created_items.append(self._item(
                    request, asset_id, "invalid_asset",
                    "asset_not_found", "Asset was not found.",
                ))
                continue
            if asset.tenant_id != tenant_id:
                created_items.append(self._item(
                    request, asset_id, "unauthorized",
                    "asset_access_denied", "Asset does not belong to this tenant.",
                ))
                continue
            if selection_error is not None or selection is None:
                created_items.append(self._item(
                    request, asset_id, "provider_unavailable",
                    "ai_provider_unavailable",
                    "The requested AI provider or mode is unavailable.",
                ))
                continue

            existing_analysis = self.metadata.find_normal_analysis(
                tenant_id=tenant_id,
                asset=asset,
                metadata_profile_id=profile.id,
                prompt_version=prompt_version,
                pipeline_version=pipeline_version,
                ai_provider=selection.provider,
                ai_model=selection.model,
            )
            if existing_analysis is None or body.force:
                allowed, estimate, reason = self._budget_preflight(
                    tenant_id=tenant_id,
                    profile=profile,
                    provider=selection.provider,
                    model=selection.model,
                    planned_cost=planned_cost,
                )
                if not allowed:
                    created_items.append(self._item(
                        request, asset_id, "budget_preflight_failed",
                        "budget_preflight_failed", reason,
                    ))
                    continue
                planned_cost += estimate
            analysis = self.metadata.create_analysis(
                tenant_id=tenant_id,
                asset_id=asset_id,
                metadata_profile_id=profile.id,
                prompt_version=prompt_version,
                pipeline_version=pipeline_version,
                ai_provider=selection.provider,
                ai_model=selection.model,
                force=body.force,
            )
            acceptance = (
                "already_exists"
                if existing_analysis is not None and not body.force
                else "accepted"
            )
            item = self._item(request, asset_id, acceptance)
            item.analysis_id = analysis.id
            created_items.append(item)
            if body.processing_mode == "single":
                job = self.processing.create_job(
                    tenant_id=tenant_id,
                    job_type="asset_analyze",
                    entity_type="asset_ai_analysis",
                    entity_id=analysis.id,
                    idempotency_key=f"asset-analyze:{analysis.id}",
                    payload={
                        "analysis_id": analysis.id,
                        "asset_id": analysis.asset_id,
                        "analysis_request_id": request.id,
                    },
                    provider_key=selection.provider,
                    provider_scope="ai",
                )
                item.processing_job_id = job.id
            else:
                batch_analyses.append(analysis)
                batch_items.append(item)

        if batch_analyses and selection is not None:
            job = self.processing.create_job(
                tenant_id=tenant_id,
                job_type="ai_batch_prepare",
                entity_type="ai_analysis_request",
                entity_id=request.id,
                idempotency_key=f"ai-batch-prepare-request:{request.id}",
                payload={
                    "analysis_request_id": request.id,
                    "analysis_ids": [value.id for value in batch_analyses],
                },
                provider_key=selection.provider,
                provider_scope="ai",
            )
            for item in batch_items:
                item.processing_job_id = job.id
        self.session.flush()
        return BulkCreateResult(request, tuple(created_items), False)

    def cancel_queued(
        self,
        *,
        tenant_id: str,
        request_id: str,
        actor_id: str,
        reason: str,
    ) -> tuple[AiAnalysisRequestModel, list[str]]:
        request = self.requests.get(tenant_id, request_id)
        batch_ids = self.requests.batch_ids(tenant_id, request_id)
        if request.status == "cancelled":
            return request, batch_ids
        items = self.requests.items(tenant_id, request_id)
        for job_id in {item.processing_job_id for item in items if item.processing_job_id}:
            self.processing.cancel_unstarted_job(
                tenant_id=tenant_id,
                job_id=job_id,
                actor_id=actor_id,
                reason=reason,
            )
        if batch_ids:
            unsubmitted_batch_ids = list(self.session.scalars(
                select(AiBatchJobModel.id).where(
                    AiBatchJobModel.tenant_id == tenant_id,
                    AiBatchJobModel.id.in_(batch_ids),
                    AiBatchJobModel.provider_batch_id.is_(None),
                )
            ))
            if unsubmitted_batch_ids:
                batch_jobs = self.session.scalars(
                    select(ProcessingJobModel).where(
                        ProcessingJobModel.tenant_id == tenant_id,
                        ProcessingJobModel.entity_type == "ai_batch_job",
                        ProcessingJobModel.entity_id.in_(unsubmitted_batch_ids),
                    )
                ).all()
                for job in batch_jobs:
                    self.processing.cancel_unstarted_job(
                        tenant_id=tenant_id, job_id=job.id,
                        actor_id=actor_id, reason=reason,
                    )

        for item in items:
            if not item.analysis_id:
                continue
            analysis = self.session.get(AssetAiAnalysisModel, item.analysis_id)
            if analysis is not None and analysis.status == "pending":
                analysis.status = "failed"
                analysis.processing_stage = "cancelled"
                analysis.last_error_code = "analysis_cancelled"
                analysis.last_error_message = "Analysis was cancelled before execution."
                analysis.failure_retryable = False
                analysis.completed_at = datetime.now(timezone.utc)
        request.status = "cancelled"
        request.cancellation_reason = reason
        request.cancelled_by = actor_id
        request.cancelled_at = datetime.now(timezone.utc)
        self.session.flush()
        return request, batch_ids

    def _item(
        self,
        request: AiAnalysisRequestModel,
        asset_id: str,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AiAnalysisRequestItemModel:
        item = AiAnalysisRequestItemModel(
            tenant_id=request.tenant_id,
            request_id=request.id,
            requested_asset_id=asset_id,
            acceptance_status=status,
            error_code=error_code,
            error_message=error_message,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def _default_model(self, provider: str) -> str:
        return (
            self.settings.GEMINI_MODEL
            if provider == "gemini"
            else self.settings.OPENAI_DEFAULT_MODEL
        )

    def _budget_preflight(
        self,
        *,
        tenant_id: str,
        profile: MetadataProfileModel,
        provider: str,
        model: str,
        planned_cost: int,
    ) -> tuple[bool, int, str | None]:
        if self.settings.AI_EMERGENCY_STOP_ENABLED:
            return False, 0, "Global emergency AI stop is enabled."
        policy = self.session.get(TenantAiBudgetPolicyModel, tenant_id)
        rate = self.governance.resolve_cost_rate(provider, model)
        input_units = max(1, (len(profile.prompt_template) + 3) // 4)
        estimate = self.governance.estimate_cost(
            rate, input_units, self.settings.AI_ESTIMATED_OUTPUT_UNITS, 1
        )
        if policy is None or not policy.enabled:
            return True, estimate, None
        if rate is None:
            return False, 0, "No cost rate is configured for this provider model."
        if rate.currency != policy.currency:
            return False, 0, "AI cost currency does not match the tenant budget."
        now = datetime.now(timezone.utc)
        periods = []
        if policy.daily_limit_micros is not None:
            periods.append(("daily", now.date().isoformat(),
                            policy.daily_limit_micros))
        if policy.monthly_limit_micros is not None:
            periods.append(("monthly", now.strftime("%Y-%m"),
                            policy.monthly_limit_micros))
        if policy.per_run_limit_micros is not None:
            hard = (
                policy.per_run_limit_micros
                * policy.hard_stop_threshold_percent // 100
            )
            if planned_cost + estimate > hard:
                return False, estimate, "The per-run AI budget would be exceeded."
        for period_type, period_key, limit in periods:
            account = self.session.scalar(
                select(AiBudgetAccountModel).where(
                    AiBudgetAccountModel.tenant_id == tenant_id,
                    AiBudgetAccountModel.period_type == period_type,
                    AiBudgetAccountModel.period_key == period_key,
                    AiBudgetAccountModel.currency == policy.currency,
                )
            )
            committed = (
                (account.actual_micros + account.reserved_micros)
                if account is not None else 0
            )
            hard = limit * policy.hard_stop_threshold_percent // 100
            if committed + planned_cost + estimate > hard:
                return False, estimate, (
                    f"The {period_type} AI budget would be exceeded."
                )
        return True, estimate, None
