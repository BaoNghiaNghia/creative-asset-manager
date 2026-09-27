from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pydantic import ValidationError

from app.core.config import Settings
from app.domain.processing.handlers import (
    DeferredJobOutcome,
    JobHandlerContext,
    JobHandlerResult,
)
from app.domain.providers.contracts import AiProviderError, OpenStoredAssetInput
from app.domain.providers.registry import AiProviderUnavailableError
from app.modules.ai_metadata.analysis_image import (
    AnalysisImageError,
    AnalysisImagePreparer,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.review import RrugcReviewService
from app.modules.realistic_review_ugc.supervisor import (
    MAX_GENERATION_ATTEMPTS,
    SupervisorAnalysisDocument,
    analyze_supervisor_sheet,
    build_comparison_sheet,
    evaluate_supervisor,
    supervisor_metrics,
)


class RrugcSupervisorQaJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(
        self, context: JobHandlerContext
    ) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except ValidationError:
            self._mark_error(context, "rrugc_supervisor_invalid_document", terminal=False)
            return JobHandlerResult.retryable(
                "rrugc_supervisor_invalid_document",
                "Supervisor returned an invalid QA document.",
            )
        except AnalysisImageError as exc:
            self._mark_error(context, exc.code, terminal=not exc.retryable)
            outcome = (
                JobHandlerResult.retryable
                if exc.retryable
                else JobHandlerResult.non_retryable
            )
            return outcome(exc.code, "Supervisor image input could not be prepared.")
        except AiProviderUnavailableError:
            self._mark_error(context, "ai_provider_unavailable", terminal=True)
            return JobHandlerResult.non_retryable(
                "ai_provider_unavailable",
                "Supervisor provider is unavailable.",
            )
        except AiProviderError as exc:
            retry_at = getattr(exc, "earliest_retry_at", None)
            self._mark_error(context, exc.code, terminal=not exc.retryable)
            if exc.retryable and isinstance(retry_at, datetime):
                return DeferredJobOutcome(
                    exc.code,
                    "Supervisor is temporarily unavailable.",
                    retry_at,
                )
            outcome = (
                JobHandlerResult.retryable
                if exc.retryable
                else JobHandlerResult.non_retryable
            )
            return outcome(exc.code, "Supervisor QA failed.")
        except Exception:
            context.logger.exception(
                "rrugc_supervisor_qa_failed",
                extra={
                    "supervisor_result_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                },
            )
            self._mark_error(
                context, "rrugc_supervisor_internal_error", terminal=False
            )
            return JobHandlerResult.retryable(
                "rrugc_supervisor_internal_error",
                "Supervisor QA failed.",
            )

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        result_id = str(context.job.payload.get("supervisor_result_id") or "")
        attempt_id = str(context.job.payload.get("generation_attempt_id") or "")
        if (
            not result_id
            or result_id != context.job.entity_id
            or not attempt_id
        ):
            return JobHandlerResult.non_retryable(
                "rrugc_supervisor_job_invalid",
                "Supervisor job payload is invalid.",
            )
        if context.is_cancelled:
            return JobHandlerResult.cancelled()

        with context.dependencies.session_factory() as session:
            repository = RrugcRepository(session)
            result = repository.lock_supervisor_result(
                context.job.tenant_id, result_id
            )
            attempt = repository.get_generation_attempt(
                context.job.tenant_id, attempt_id
            )
            if result is None or attempt is None:
                return JobHandlerResult.non_retryable(
                    "rrugc_supervisor_source_not_found",
                    "Supervisor source generation was not found.",
                )
            if result.status in {"pass", "fail", "needs_human_review"}:
                if result.status in {"pass", "needs_human_review"}:
                    try:
                        RrugcReviewService(session).ensure_from_supervisor(
                            result=result
                        )
                    except Exception:
                        context.logger.exception(
                            "rrugc_review_handoff_failed",
                            extra={
                                "supervisor_result_id": result.id,
                                "generation_attempt_id": attempt.id,
                                "tenant_id": context.job.tenant_id,
                            },
                        )
                        return JobHandlerResult.retryable(
                            "rrugc_review_handoff_failed",
                            "Supervisor completed but review handoff could not be persisted.",
                        )
                return JobHandlerResult.completed()
            if attempt.status != "completed" or not attempt.output_remote_file_id:
                return JobHandlerResult.retryable(
                    "rrugc_supervisor_output_not_ready",
                    "Generated output is not ready for Supervisor QA.",
                )
            product_references = list(
                attempt.product_reference_snapshot_json or []
            )
            candidate_snapshot = dict(attempt.candidate_snapshot_json or {})
            source_person_remote_file_id = str(
                candidate_snapshot.get("remote_file_id") or ""
            )
            if not source_person_remote_file_id:
                return JobHandlerResult.non_retryable(
                    "rrugc_supervisor_person_reference_missing",
                    "Frozen person reference is unavailable.",
                )
            if not product_references:
                return JobHandlerResult.non_retryable(
                    "rrugc_supervisor_product_reference_missing",
                    "Frozen product references are unavailable.",
                )
            result.status = "running"
            result.last_error_code = None
            result.last_error_message = None
            session.commit()
            output_remote_file_id = attempt.output_remote_file_id
            output_content_type = attempt.output_content_type
            output_size_bytes = attempt.output_size_bytes

        storage = context.dependencies.storage_provider
        if storage is None:
            raise AnalysisImageError(
                "Managed storage is unavailable.",
                code="managed_storage_unavailable",
                retryable=True,
            )
        preparer = AnalysisImagePreparer(storage)
        generated = await preparer.prepare(
            OpenStoredAssetInput(
                tenant_id=context.job.tenant_id,
                asset_id=f"rrugc-generation:{attempt_id}",
                remote_file_id=output_remote_file_id,
                content_type=output_content_type,
                size_bytes=output_size_bytes,
            )
        )
        person_source = await preparer.prepare(
            OpenStoredAssetInput(
                tenant_id=context.job.tenant_id,
                asset_id=f"rrugc-person-reference:{attempt.candidate_id}",
                remote_file_id=source_person_remote_file_id,
                size_bytes=(
                    int(candidate_snapshot["size_bytes"])
                    if candidate_snapshot.get("size_bytes") is not None
                    else None
                ),
            )
        )
        reference_images: list[tuple[str, bytes]] = []
        for item in product_references[:4]:
            remote_file_id = str(item.get("remote_file_id") or "")
            if not remote_file_id:
                continue
            prepared = await preparer.prepare(
                OpenStoredAssetInput(
                    tenant_id=context.job.tenant_id,
                    asset_id=(
                        "rrugc-product-reference:"
                        + str(item.get("id") or item.get("view_type") or "unknown")
                    ),
                    remote_file_id=remote_file_id,
                    content_type=(
                        str(item.get("content_type"))
                        if item.get("content_type")
                        else None
                    ),
                    size_bytes=(
                        int(item["size_bytes"])
                        if item.get("size_bytes") is not None
                        else None
                    ),
                )
            )
            reference_images.append(
                (str(item.get("view_type") or "reference"), prepared.content)
            )
        sheet, width, height, labels = build_comparison_sheet(
            generated.content,
            reference_images,
            person_source=person_source.content,
        )

        registry = context.dependencies.ai_provider_registry
        if registry is None:
            raise AiProviderUnavailableError("gemini")
        provider = registry.require("gemini")
        document, provider_name, provider_model = await analyze_supervisor_sheet(
            provider=provider,
            tenant_id=context.job.tenant_id,
            result_id=result_id,
            image_bytes=sheet,
            width=width,
            height=height,
            reference_labels=labels,
        )
        decision = evaluate_supervisor(document)

        with context.dependencies.session_factory() as session:
            repository = RrugcRepository(session)
            result = repository.lock_supervisor_result(
                context.job.tenant_id, result_id
            )
            attempt = repository.get_generation_attempt(
                context.job.tenant_id, attempt_id
            )
            if result is None or attempt is None:
                return JobHandlerResult.non_retryable(
                    "rrugc_supervisor_source_not_found",
                    "Supervisor source generation was not found.",
                )
            attempts = repository.list_generation_attempts(
                context.job.tenant_id,
                attempt.campaign_id,
                candidate_id=attempt.candidate_id,
                limit=100,
            )
            final_status = decision.status
            if (
                final_status == "fail"
                and len(attempts) >= MAX_GENERATION_ATTEMPTS
            ):
                final_status = "needs_human_review"
            result.status = final_status
            result.reason = decision.reason
            result.metrics_json = supervisor_metrics(document)
            result.expected_json = dict(decision.expected)
            result.correction_json = (
                dict(decision.correction) if decision.correction else None
            )
            result.summary = document.summary
            result.provider = provider_name
            result.provider_model = provider_model
            result.last_error_code = None
            result.last_error_message = None
            result.completed_at = datetime.now(timezone.utc)
            session.commit()
            if final_status in {"pass", "needs_human_review"}:
                try:
                    RrugcReviewService(session).ensure_from_supervisor(
                        result=result
                    )
                except Exception:
                    context.logger.exception(
                        "rrugc_review_handoff_failed",
                        extra={
                            "supervisor_result_id": result.id,
                            "generation_attempt_id": attempt.id,
                            "tenant_id": context.job.tenant_id,
                        },
                    )
                    return JobHandlerResult.retryable(
                        "rrugc_review_handoff_failed",
                        "Supervisor completed but review handoff could not be persisted.",
                    )

        context.logger.info(
            "rrugc_supervisor_qa_completed",
            extra={
                "supervisor_result_id": result_id,
                "generation_attempt_id": attempt_id,
                "tenant_id": context.job.tenant_id,
                "status": final_status,
                "reason": decision.reason,
                "supervisor_version": result.supervisor_skill_version,
            },
        )
        return JobHandlerResult.completed()

    @staticmethod
    def _mark_error(
        context: JobHandlerContext,
        code: str,
        *,
        terminal: bool,
    ) -> None:
        result_id = str(context.job.payload.get("supervisor_result_id") or "")
        if not result_id:
            return
        with context.dependencies.session_factory() as session:
            result = RrugcRepository(session).lock_supervisor_result(
                context.job.tenant_id, result_id
            )
            if result is None:
                return
            result.status = "error" if terminal else "queued"
            result.last_error_code = code
            result.last_error_message = "Supervisor QA failed."
            session.commit()
