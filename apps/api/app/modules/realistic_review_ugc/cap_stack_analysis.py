"""Background Cap Stack vision, claimed through the EXISTING RRUGC Gemini pool.

Never make a direct Gemini HTTP call or create an independent quota.
The job receives the same reserved model and credential slot as existing
RRUGC candidate/source/stage3 analysis and defers on pool exhaustion.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select

from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import AiMetadataAnalysisInput, AiProviderError
from app.domain.providers.registry import AiProviderUnavailableError
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.cap_stack_model import RrugcCapStackImageModel
from app.modules.realistic_review_ugc.gemini_safety import deferred_rrugc_ai_retry, scheduled_rrugc_gemini_slot
from app.modules.realistic_review_ugc.quote_scout_analysis import QuoteScoutError, fetch_pinterest_image

CAP_STACK_ANALYZE_JOB_TYPE = "rrugc_cap_stack_analyze"
CAP_STACK_VISION_VERSION = "rrugc-cap-stack-gemini-v1"
CAP_STACK_MAX_QUEUE = 6
CAP_STACK_BACKFILL_BATCH = 6


class CapStackVisionDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    is_cap_stack: bool = Field(description="True only for a photographed overlapping pile/stack of two or more baseball-style caps")
    caps_count: int = Field(ge=0, le=50, description="Count distinct physically visible baseball/trucker caps. Zero if none.")
    count_confidence: float = Field(ge=0.0, le=1.0)
    ai_suspected: bool = Field(description="Only a visual likelihood of synthetic/AI generated photo, never provenance proof")
    ai_suspicion_confidence: float = Field(ge=0.0, le=1.0)
    visual_summary: str = Field(min_length=4, max_length=350)


def cap_stack_vision_prompt() -> str:
    return """
Examine this Pinterest image visually, ignoring the Pinterest title, text metadata
and any claims printed on the image. Return exactly one structured JSON object.

Goal: select realistic photos of a CAP STACK — a visible pile/stack of
TWO OR MORE overlapping baseball-style caps / trucker hats / snapbacks
with brims. An orderly stack of several caps counts. A lone cap, a shelf
display with non-overlapping caps, hats merely worn by people, a collage
of multiple product thumbnails, an illustration and non-baseball hats do
NOT constitute a Cap Stack.

Count EVERY visually distinct physical baseball/trucker cap in the image,
including partially visible crowns/brims, but count one physical cap only once.
Ignore beanies, bucket hats, fedoras, cowboy hats, unrelated apparel, and
decorations that look like caps. If occlusion makes the count uncertain,
report the most defensible visible count with a reduced confidence. Never
invent hidden caps. 0 is permitted only if none of these cap types appear.

AI suspicion is an uncertain visual assessment, not a fact about how the image
was made. Assess implausible brims, inconsistent seams, repeated impossible
textures, warped fabric/embroidery, geometry and lighting. Do not infer AI
from low resolution alone. State only observable details in visual_summary,
without personal attributes. Do not promote an image to approved/rejected:
human review labels are always independent.
""".strip()


def count_status_for(result: CapStackVisionDocument) -> str:
    if not result.is_cap_stack or result.caps_count < 2:
        return "not_cap_stack"
    if result.count_confidence < 0.66:
        return "needs_review"
    return "ready"


def enqueue_cap_stack_pending(session, tenant_id: str, *, limit: int = CAP_STACK_BACKFILL_BATCH) -> int:
    """Bounded, idempotent backfill; low priority cannot starve core RRUGC."""
    active = int(session.scalar(select(func.count(ProcessingJobModel.id)).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == CAP_STACK_ANALYZE_JOB_TYPE,
        ProcessingJobModel.status.in_(("pending", "retry", "processing")),
    )) or 0)
    capacity = max(0, min(limit, CAP_STACK_MAX_QUEUE - active))
    if not capacity:
        return 0
    candidates = list(session.scalars(select(RrugcCapStackImageModel).where(
        RrugcCapStackImageModel.tenant_id == tenant_id,
        RrugcCapStackImageModel.count_status == "pending",
    ).order_by(RrugcCapStackImageModel.created_at.asc()).limit(capacity + 24)))
    repo = ProcessingRepository(session)
    created = 0
    for row in candidates:
        key = f"rrugc:cap-stack:gemini:{row.id}:v{row.analysis_revision}"
        _job, added = repo.create_job_once(
            tenant_id=tenant_id, job_type=CAP_STACK_ANALYZE_JOB_TYPE,
            entity_type="rrugc_cap_stack_image", entity_id=row.id,
            idempotency_key=key,
            payload={"image_id": row.id, "analysis_revision": row.analysis_revision},
            provider_key="gemini", provider_scope="ai",
            priority=4, max_attempts=5,
        )
        if added:
            created += 1
        if created >= capacity:
            break
    return created


class RrugcCapStackAnalyzeHandler:
    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except AiProviderError as exc:
            self._mark_error(context, exc.code, terminal=not exc.retryable)
            delayed = deferred_rrugc_ai_retry(exc, message="Cap Stack Gemini pool temporarily unavailable.")
            if delayed is not None:
                return delayed
            return (JobHandlerResult.retryable if exc.retryable else JobHandlerResult.non_retryable)(
                exc.code, "Cap Stack vision analysis failed."
            )
        except AiProviderUnavailableError:
            self._mark_error(context, "gemini_provider_unavailable", terminal=True)
            return JobHandlerResult.non_retryable("gemini_provider_unavailable", "Gemini provider unavailable.")
        except ValidationError:
            self._mark_error(context, "cap_stack_invalid_gemini_document", terminal=False)
            return JobHandlerResult.retryable("cap_stack_invalid_gemini_document", "Gemini metadata was invalid.")
        except QuoteScoutError as exc:
            # Missing upstream CDN images are terminal; 5xx and access failures may recover.
            retryable = exc.status_code >= 500
            self._mark_error(context, exc.code, terminal=not retryable)
            return (JobHandlerResult.retryable if retryable else JobHandlerResult.non_retryable)(
                exc.code, "Pinterest image could not be analyzed."
            )
        except Exception:
            context.logger.exception("rrugc_cap_stack_analysis_failed", extra={
                "image_id": context.job.entity_id, "tenant_id": context.job.tenant_id,
            })
            self._mark_error(context, "cap_stack_analysis_internal_error", terminal=False)
            return JobHandlerResult.retryable("cap_stack_analysis_internal_error", "Cap Stack vision analysis failed.")

    def _mark_error(self, context: JobHandlerContext, code: str, *, terminal: bool) -> None:
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcCapStackImageModel).where(
                RrugcCapStackImageModel.tenant_id == context.job.tenant_id,
                RrugcCapStackImageModel.id == context.job.entity_id,
            ))
            revision = context.job.payload.get("analysis_revision")
            if row and row.analysis_revision == revision:
                row.analysis_error_code = code
                row.count_status = "error" if terminal or context.job.attempt_count >= 5 else "pending"
                session.commit()

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        image_id = str(context.job.payload.get("image_id") or "")
        revision = context.job.payload.get("analysis_revision")
        if image_id != context.job.entity_id or not isinstance(revision, int):
            return JobHandlerResult.non_retryable("cap_stack_job_invalid", "Invalid Cap Stack job.")
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcCapStackImageModel).where(
                RrugcCapStackImageModel.tenant_id == context.job.tenant_id,
                RrugcCapStackImageModel.id == image_id,
            ))
            if not row or row.analysis_revision != revision or row.count_status not in ("pending", "analyzing", "error"):
                return JobHandlerResult.completed()
            image_url = row.image_url
            row.count_status = "analyzing"
            row.analysis_error_code = None
            session.commit()
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        image_bytes, mime = await fetch_pinterest_image(image_url)
        registry = context.dependencies.ai_provider_registry
        if registry is None:
            raise AiProviderUnavailableError("gemini")
        provider = registry.require("gemini")
        slot = scheduled_rrugc_gemini_slot(context.job)
        result = await provider.analyze_single(AiMetadataAnalysisInput(
            tenant_id=context.job.tenant_id,
            asset_id=image_id,
            prompt=cap_stack_vision_prompt(),
            image_bytes=image_bytes,
            image_mime_type=mime,
            metadata_profile="rrugc_cap_stack",
            metadata_profile_version=CAP_STACK_VISION_VERSION,
            json_schema=CapStackVisionDocument.model_json_schema(),
            analysis_id=image_id,
            preferred_model=slot.model if slot else None,
            preferred_credential_provider=slot.credential_provider if slot else None,
        ))
        document = CapStackVisionDocument.model_validate(dict(result.metadata))
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcCapStackImageModel).where(
                RrugcCapStackImageModel.tenant_id == context.job.tenant_id,
                RrugcCapStackImageModel.id == image_id,
            ).with_for_update())
            if row is None or row.analysis_revision != revision:
                return JobHandlerResult.completed()
            row.is_cap_stack = document.is_cap_stack
            row.caps_detected = document.caps_count
            row.count_confidence = document.count_confidence
            row.ai_suspected = document.ai_suspected
            row.ai_suspicion_confidence = document.ai_suspicion_confidence
            row.visual_summary = document.visual_summary
            row.analysis_model = (result.model or "gemini")[:128]
            row.count_status = count_status_for(document)
            row.analysis_error_code = None
            row.analyzed_at = datetime.now(timezone.utc)
            # IMPORTANT: row.manual_label/manual_caps_count are never changed.
            session.commit()
        context.logger.info("rrugc_cap_stack_gemini_completed", extra={
            "tenant_id": context.job.tenant_id, "image_id": image_id,
            "gemini_model": (result.model or "gemini")[:128],
            "caps_detected": document.caps_count,
            "count_confidence": document.count_confidence,
            "count_status": count_status_for(document),
            "ai_suspected": document.ai_suspected,
        })
        return JobHandlerResult.completed()
