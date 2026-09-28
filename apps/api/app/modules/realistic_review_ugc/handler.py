from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import (
    DeferredJobOutcome,
    JobHandlerContext,
    JobHandlerResult,
)
from app.domain.providers.contracts import AiProviderError
from app.domain.providers.registry import AiProviderUnavailableError
from app.infrastructure.downloader.secure_image import (
    DownloadLimitError,
    InvalidImageError,
    SecureDownloadError,
    UnsafeUrlError,
)
from app.modules.realistic_review_ugc.analysis import (
    ANALYZER_VERSION,
    ReferenceAnalysisDocument,
    analyze_reference_image,
    assess_ai_risk,
    build_ai_risk_calibration,
    confirm_ai_authenticity,
    evaluate_reference,
    policy_from_campaign,
    should_confirm_ai_risk,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.service import (
    RrugcError,
    RrugcService,
    build_reference_downloader,
)


def _image_mime(image_format: str) -> str:
    return {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "GIF": "image/gif",
        "TIFF": "image/tiff",
        "BMP": "image/bmp",
    }.get(image_format.upper(), "application/octet-stream")


class RrugcCandidateAnalyzeJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except (UnsafeUrlError, DownloadLimitError, InvalidImageError) as exc:
            self._mark_error(context, exc.__class__.__name__, terminal=True)
            return JobHandlerResult.non_retryable(
                "rrugc_analysis_source_rejected",
                "Reference image is not safe or valid for analysis.",
            )
        except (SecureDownloadError, httpx.HTTPError) as exc:
            self._mark_error(context, exc.__class__.__name__, terminal=False)
            return JobHandlerResult.retryable(
                "rrugc_analysis_source_unavailable",
                "Reference image could not be downloaded for analysis.",
            )
        except ValidationError:
            self._mark_error(context, "rrugc_analysis_invalid_document", terminal=False)
            return JobHandlerResult.retryable(
                "rrugc_analysis_invalid_document",
                "Reference analyzer returned an invalid document.",
            )
        except AiProviderUnavailableError:
            self._mark_error(context, "ai_provider_unavailable", terminal=True)
            return JobHandlerResult.non_retryable(
                "ai_provider_unavailable",
                "Reference analyzer provider is unavailable.",
            )
        except AiProviderError as exc:
            retry_at = getattr(exc, "earliest_retry_at", None)
            self._mark_error(context, exc.code, terminal=not exc.retryable)
            if exc.retryable and isinstance(retry_at, datetime):
                return DeferredJobOutcome(
                    exc.code,
                    "Reference analyzer is temporarily unavailable.",
                    retry_at,
                )
            outcome = JobHandlerResult.retryable if exc.retryable else JobHandlerResult.non_retryable
            return outcome(exc.code, "Reference analyzer failed.")
        except Exception:
            context.logger.exception(
                "rrugc_candidate_analysis_failed",
                extra={
                    "candidate_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                    "error_code": "rrugc_analysis_internal_error",
                },
            )
            self._mark_error(context, "rrugc_analysis_internal_error", terminal=False)
            return JobHandlerResult.retryable(
                "rrugc_analysis_internal_error",
                "Reference analysis failed.",
            )

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        candidate_id = str(context.job.payload.get("candidate_id") or "")
        campaign_id = str(context.job.payload.get("campaign_id") or "")
        revision = context.job.payload.get("analysis_revision")
        if (
            not candidate_id
            or candidate_id != context.job.entity_id
            or not campaign_id
            or not isinstance(revision, int)
        ):
            return JobHandlerResult.non_retryable(
                "rrugc_analysis_job_invalid",
                "Reference analysis job payload is invalid.",
            )
        if context.is_cancelled:
            return JobHandlerResult.cancelled()

        with context.dependencies.session_factory() as session:
            repository = RrugcRepository(session)
            candidate = repository.get_candidate(
                context.job.tenant_id, campaign_id, candidate_id
            )
            campaign = repository.get_campaign(context.job.tenant_id, campaign_id)
            if candidate is None or campaign is None:
                return JobHandlerResult.non_retryable(
                    "rrugc_candidate_not_found",
                    "Reference candidate was not found.",
                )
            if candidate.analysis_revision != revision:
                return JobHandlerResult.completed()
            if candidate.status in {
                "approved",
                "import_queued",
                "importing",
                "drive_ready",
                "rejected_duplicate",
                "rejected_no_person",
                "rejected_head_ratio",
                "rejected_expression",
                "rejected_existing_headwear",
                "rejected_head_occlusion",
                "rejected_quality",
                "rejected_ai_risk",
                "rejected_context",
            } and candidate.analyzed_at is not None:
                return JobHandlerResult.completed()
            candidate.status = "analyzing"
            candidate.last_error_code = None
            session.commit()
            image_url = candidate.image_url
            policy = policy_from_campaign(campaign)
            manual_ai_label = candidate.ai_manual_label
            calibration = build_ai_risk_calibration(
                repository.ai_feedback_training_rows(context.job.tenant_id)
            )

        registry = context.dependencies.ai_provider_registry
        if registry is None:
            raise AiProviderUnavailableError("gemini")
        provider = registry.require("gemini")
        downloader = build_reference_downloader()
        async with downloader.download(image_url) as image:
            if context.is_cancelled:
                return JobHandlerResult.cancelled()
            image_bytes = image.path.read_bytes()
            image_mime_type = _image_mime(image.image_format)
            document, provider_name, model = await analyze_reference_image(
                provider=provider,
                tenant_id=context.job.tenant_id,
                candidate_id=candidate_id,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
                width=image.width,
                height=image.height,
            )
            confirmation = None
            if manual_ai_label not in {"real", "ai"} and should_confirm_ai_risk(
                document, policy
            ):
                confirmation = await confirm_ai_authenticity(
                    provider=provider,
                    tenant_id=context.job.tenant_id,
                    candidate_id=candidate_id,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                    width=image.width,
                    height=image.height,
                )
            ai_assessment = assess_ai_risk(
                document,
                confirmation=confirmation,
                calibration=calibration,
            )
            decision = evaluate_reference(
                document,
                policy,
                effective_ai_risk_score=ai_assessment.calibrated_score,
                ai_evidence_count=ai_assessment.strong_evidence_count,
                ai_detector_confidence=ai_assessment.detector_confidence,
                ai_risk_confirmed=ai_assessment.confirmed,
                manual_ai_label=manual_ai_label,
            )

            with context.dependencies.session_factory() as session:
                repository = RrugcRepository(session)
                candidate = repository.get_candidate(
                    context.job.tenant_id, campaign_id, candidate_id
                )
                campaign = repository.get_campaign(context.job.tenant_id, campaign_id)
                if candidate is None or campaign is None:
                    return JobHandlerResult.non_retryable(
                        "rrugc_candidate_not_found",
                        "Reference candidate was not found.",
                    )
                if candidate.analysis_revision != revision:
                    return JobHandlerResult.completed()

                self._apply_document(
                    candidate,
                    document,
                    ai_assessment,
                    decision.status,
                    decision.reject_reason,
                    decision.final_score,
                    provider_name,
                    model,
                    image.content_hash,
                    image.width,
                    image.height,
                    image.size_bytes,
                    image.image_format,
                )
                service = RrugcService(session)
                if decision.approved and campaign.auto_import:
                    service.enqueue_import(candidate)
                service.refresh_campaign_completion(campaign)
                session.commit()

        context.logger.info(
            "rrugc_candidate_analysis_completed",
            extra={
                "candidate_id": candidate_id,
                "tenant_id": context.job.tenant_id,
                "status": decision.status,
                "final_score": decision.final_score,
                "reject_reason": decision.reject_reason,
                "analyzer_version": ANALYZER_VERSION,
            },
        )
        return JobHandlerResult.completed()

    @staticmethod
    def _apply_document(
        candidate,
        document: ReferenceAnalysisDocument,
        ai_assessment,
        status: str,
        reject_reason: str | None,
        final_score: float,
        provider_name: str,
        model: str | None,
        content_hash: str,
        width: int,
        height: int,
        size_bytes: int,
        image_format: str,
    ) -> None:
        candidate.status = status
        candidate.people_count = document.people_count
        candidate.primary_head_ratio = document.primary_head_ratio
        candidate.smile_score = document.smile_score
        candidate.head_visible = document.head_visible
        candidate.existing_headwear = document.existing_headwear
        candidate.head_occlusion = document.head_occlusion
        candidate.mobile_ugc_score = document.mobile_ugc_score
        candidate.phone_authenticity_score = document.phone_authenticity_score
        candidate.artistic_editorial_risk = document.artistic_editorial_risk
        candidate.quality_score = document.quality_score
        candidate.ai_risk_score = ai_assessment.calibrated_score
        candidate.ai_risk_raw_score = ai_assessment.raw_score
        candidate.ai_detector_confidence = ai_assessment.detector_confidence
        candidate.ai_risk_confirmed = ai_assessment.confirmed
        candidate.ai_signal_json = ai_assessment.signal_json
        candidate.product_fit_score = document.product_fit_score
        candidate.final_score = final_score
        candidate.reject_reason = reject_reason
        candidate.analyzer_provider = provider_name
        candidate.analyzer_model = model
        candidate.analyzer_version = ANALYZER_VERSION
        candidate.analysis_summary = document.summary
        candidate.analyzed_at = datetime.now(timezone.utc)
        candidate.last_error_code = None
        candidate.content_hash = content_hash
        candidate.width = width
        candidate.height = height
        candidate.size_bytes = size_bytes
        candidate.image_format = image_format

    @staticmethod
    def _mark_error(context: JobHandlerContext, code: str, *, terminal: bool) -> None:
        candidate_id = str(context.job.payload.get("candidate_id") or "")
        campaign_id = str(context.job.payload.get("campaign_id") or "")
        revision = context.job.payload.get("analysis_revision")
        if not candidate_id or not campaign_id:
            return
        with context.dependencies.session_factory() as session:
            candidate = RrugcRepository(session).get_candidate(
                context.job.tenant_id, campaign_id, candidate_id
            )
            if candidate is None or candidate.analysis_revision != revision:
                return
            # Keep the durable UI state truthful even when the Processing Job
            # will retry automatically. A later attempt moves this back to
            # "analyzing"; exhausted retries therefore never leave a candidate
            # looking permanently queued.
            candidate.status = "analysis_failed"
            candidate.last_error_code = code[:100]
            session.commit()


class RrugcCandidateImportJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        try:
            return asyncio.run(self._execute(context))
        except RrugcError as exc:
            outcome = JobHandlerResult.retryable if exc.retryable else JobHandlerResult.non_retryable
            return outcome(exc.code, str(exc))
        except Exception:
            context.logger.exception(
                "rrugc_candidate_import_failed",
                extra={
                    "candidate_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                    "error_code": "rrugc_import_internal_error",
                },
            )
            return JobHandlerResult.retryable(
                "rrugc_import_internal_error",
                "Reference import failed.",
            )

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        candidate_id = str(context.job.payload.get("candidate_id") or "")
        campaign_id = str(context.job.payload.get("campaign_id") or "")
        revision = context.job.payload.get("import_revision")
        if (
            not candidate_id
            or candidate_id != context.job.entity_id
            or not campaign_id
            or not isinstance(revision, int)
        ):
            return JobHandlerResult.non_retryable(
                "rrugc_import_job_invalid",
                "Reference import job payload is invalid.",
            )
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        storage = context.dependencies.storage_provider
        if storage is None:
            return JobHandlerResult.retryable(
                "managed_storage_unavailable",
                "Managed storage is unavailable.",
            )

        with context.dependencies.session_factory() as session:
            repository = RrugcRepository(session)
            candidate = repository.get_candidate(
                context.job.tenant_id, campaign_id, candidate_id
            )
            if candidate is None:
                return JobHandlerResult.non_retryable(
                    "rrugc_candidate_not_found",
                    "Reference candidate was not found.",
                )
            if candidate.import_revision != revision:
                return JobHandlerResult.completed()
            if candidate.status in {"drive_ready", "rejected_duplicate"}:
                return JobHandlerResult.completed()
            await RrugcService(session).import_candidate(
                candidate=candidate,
                storage=storage,
            )

        context.logger.info(
            "rrugc_candidate_import_completed",
            extra={
                "candidate_id": candidate_id,
                "tenant_id": context.job.tenant_id,
            },
        )
        return JobHandlerResult.completed()
