from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.modules.assets.content_resolver import SourceAssetContentResolver, SourceAssetContentUnavailable
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel

from .gateway_client import DolaGatewayClient, DolaGatewayError, GatewayReference
from .model import VideoGenerationReferenceModel
from .repository import VideoGenerationRepository, VideoGenerationStateError
from .service import MAX, MIMES, TOTAL, enabled


class VideoGenerationHandlerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class VideoGenerateJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        try:
            executor = context.dependencies.resources.get("async_executor")
            if executor is not None:
                return executor.run(self._execute(context))
            return asyncio.run(self._execute(context))
        except VideoGenerationHandlerError as exc:
            if not exc.retryable:
                self._fail(context, exc.code, str(exc))
            return (
                JobHandlerResult.retryable(exc.code, str(exc))
                if exc.retryable else JobHandlerResult.non_retryable(exc.code, str(exc))
            )
        except Exception:
            self._fail(context, "video_generation_internal_error", "Video generation failed.")
            return JobHandlerResult.non_retryable(
                "video_generation_internal_error", "Video generation failed."
            )

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        settings = self.settings or get_settings()
        run_id = context.job.payload.get("video_generation_run_id")
        if context.job.job_type != "video_generate" or not isinstance(run_id, str) or run_id != context.job.entity_id:
            raise VideoGenerationHandlerError(
                "invalid_video_generation_job", "Video generation job payload is invalid."
            )
        if not enabled(settings, context.job.tenant_id):
            raise VideoGenerationHandlerError(
                "video_generation_tenant_disabled", "Video generation is disabled for this tenant."
            )

        with context.dependencies.session_factory() as session:
            repository = VideoGenerationRepository(session)
            run = repository.get(context.job.tenant_id, run_id)
            if run is None:
                raise VideoGenerationHandlerError("video_generation_not_found", "Video generation was not found.")
            state, gateway_id = run.status, run.gateway_generation_id
            if state == "completed":
                return JobHandlerResult.completed()
            if state == "failed":
                return JobHandlerResult.non_retryable(
                    run.last_error_code or "video_generation_failed",
                    run.last_error_message or "Video generation failed.",
                )
            if state == "cancelled" or context.is_cancelled:
                return JobHandlerResult.cancelled("Video generation was cancelled.")

        if state == "storing":
            return self._defer("video_generation_content_import_pending", "Video content import is pending.", settings)
        if state == "submission_unknown" and not gateway_id:
            return self._defer("video_generation_recovery_required", "Video generation requires recovery.", settings)

        client = self._client(context, settings)
        try:
            if gateway_id:
                gateway = await client.get_generation(gateway_id)
            else:
                references = await self._prepare_references(context, run_id)
                with context.dependencies.session_factory() as session:
                    repository = VideoGenerationRepository(session)
                    run = repository.get(context.job.tenant_id, run_id)
                    if run is None:
                        raise VideoGenerationHandlerError("video_generation_not_found", "Video generation was not found.")
                    if run.status == "queued":
                        repository.transition(run, "preparing")
                        session.commit()
                    if run.status == "submission_unknown":
                        return self._defer("video_generation_recovery_required", "Video generation requires recovery.", settings)
                    gateway = await client.submit(
                        run_id=run.id, prompt=run.prompt, model=run.provider_model,
                        aspect_ratio=run.aspect_ratio, duration_seconds=run.duration_seconds,
                        references=references,
                    )
                self._persist_gateway_identity(context, run_id, gateway.generation_id)
            return self._apply_gateway_state(context, run_id, gateway, settings)
        except DolaGatewayError as exc:
            raise VideoGenerationHandlerError(exc.code, str(exc), retryable=exc.retryable) from exc
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    def _client(self, context, settings):
        factory = context.dependencies.resources.get("dola_gateway_client_factory")
        return factory(settings) if callable(factory) else DolaGatewayClient(settings)

    async def _prepare_references(self, context: JobHandlerContext, run_id: str) -> list[GatewayReference]:
        with context.dependencies.session_factory() as session:
            rows = list(session.execute(
                select(VideoGenerationReferenceModel, SourceAssetModel)
                .join(
                    AssetSourceLinkModel,
                    (AssetSourceLinkModel.tenant_id == VideoGenerationReferenceModel.tenant_id)
                    & (AssetSourceLinkModel.asset_id == VideoGenerationReferenceModel.asset_id),
                )
                .join(
                    SourceAssetModel,
                    (SourceAssetModel.tenant_id == AssetSourceLinkModel.tenant_id)
                    & (SourceAssetModel.id == AssetSourceLinkModel.source_asset_id),
                )
                .where(
                    VideoGenerationReferenceModel.tenant_id == context.job.tenant_id,
                    VideoGenerationReferenceModel.run_id == run_id,
                    SourceAssetModel.deleted_at.is_(None),
                )
                .order_by(VideoGenerationReferenceModel.position, SourceAssetModel.created_at, SourceAssetModel.id)
            ))
        # A single deterministic source is selected for every persisted reference.
        source_by_asset: dict[str, str] = {}
        for reference, source in rows:
            source_by_asset.setdefault(reference.asset_id, source.id)
        with context.dependencies.session_factory() as session:
            references = list(session.scalars(
                select(VideoGenerationReferenceModel).where(
                    VideoGenerationReferenceModel.tenant_id == context.job.tenant_id,
                    VideoGenerationReferenceModel.run_id == run_id,
                ).order_by(VideoGenerationReferenceModel.position)
            ))
        source_ids = [source_by_asset.get(reference.asset_id) for reference in references]
        if any(source_id is None for source_id in source_ids):
            raise VideoGenerationHandlerError("video_reference_unavailable", "A reference image is unavailable.")

        resolver = context.dependencies.resources.get("video_generation_content_resolver")
        resolver = resolver or SourceAssetContentResolver(context.dependencies.session_factory)
        prepared: list[GatewayReference] = []
        total = 0
        for index, reference in enumerate(references):
            data = bytearray()
            try:
                async with resolver.open(tenant_id=context.job.tenant_id, source_asset_id=source_ids[index]) as stream:
                    declared_mime = stream.content_type.split(";", 1)[0].lower()
                    async for block in stream.body:
                        data.extend(block)
                        if len(data) > MAX:
                            raise VideoGenerationHandlerError("video_reference_too_large", "Reference image exceeds size limit.")
            except SourceAssetContentUnavailable as exc:
                raise VideoGenerationHandlerError("video_reference_unavailable", "A reference image is unavailable.") from exc
            total += len(data)
            if total > TOTAL:
                raise VideoGenerationHandlerError("video_reference_too_large", "Reference images exceed size limit.")
            if declared_mime not in MIMES:
                raise VideoGenerationHandlerError("video_reference_unsupported", "Reference image type is unsupported.")
            try:
                with Image.open(BytesIO(data)) as image:
                    image.verify()
                    detected = Image.MIME.get(image.format or "", "").lower()
            except Exception as exc:
                raise VideoGenerationHandlerError("video_reference_unsupported", "Reference image could not be decoded.") from exc
            if detected != declared_mime or detected not in MIMES:
                raise VideoGenerationHandlerError("video_reference_unsupported", "Reference image MIME is unsupported or mismatched.")
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[detected]
            prepared.append(GatewayReference(f"reference-{index}.{ext}", detected, bytes(data)))
        return prepared

    def _persist_gateway_identity(self, context, run_id: str, gateway_id: str) -> None:
        with context.dependencies.session_factory() as session:
            repository = VideoGenerationRepository(session)
            run = repository.get(context.job.tenant_id, run_id)
            if run is None:
                raise VideoGenerationHandlerError("video_generation_not_found", "Video generation was not found.")
            if run.gateway_generation_id and run.gateway_generation_id != gateway_id:
                raise VideoGenerationHandlerError(
                    "video_generation_gateway_identity_conflict",
                    "Video generation gateway identity conflicts.",
                )
            run.gateway_generation_id = gateway_id
            session.commit()

    def _apply_gateway_state(self, context, run_id, gateway, settings):
        target = {
            "accepted": "submitted", "submitted": "submitted", "running": "running",
            "submission_unknown": "submission_unknown", "completed": "storing",
            "failed": "failed",
        }[gateway.status]
        with context.dependencies.session_factory() as session:
            repository = VideoGenerationRepository(session)
            run = repository.get(context.job.tenant_id, run_id)
            if run is None:
                raise VideoGenerationHandlerError("video_generation_not_found", "Video generation was not found.")
            if target != run.status:
                try:
                    repository.transition(run, target)
                except VideoGenerationStateError as exc:
                    raise VideoGenerationHandlerError("video_generation_state_conflict", "Video generation state conflicts.") from exc
            if target in {"failed", "submission_unknown"}:
                run.last_error_code = gateway.error_code or (
                    "upstream_submission_state_unknown" if target == "submission_unknown" else "dola_generation_failed"
                )
                run.last_error_message = (gateway.error_message or "Video generation failed.")[:500]
            session.commit()
        if target == "failed":
            return JobHandlerResult.non_retryable(
                gateway.error_code or "dola_generation_failed", "Video generation failed."
            )
        if target == "storing":
            return self._defer("video_generation_content_import_pending", "Video content import is pending.", settings)
        if target == "submission_unknown":
            return self._defer("upstream_submission_state_unknown", "Video generation submission state is unknown.", settings)
        return self._defer("video_generation_provider_running", "Video generation is running.", settings)

    @staticmethod
    def _defer(code: str, message: str, settings) -> DeferredJobOutcome:
        seconds = max(5, int(settings.VIDEO_GENERATION_POLL_SECONDS))
        return DeferredJobOutcome(code, message, datetime.now(timezone.utc) + timedelta(seconds=seconds))

    def _fail(self, context, code: str, message: str) -> None:
        with context.dependencies.session_factory() as session:
            run = VideoGenerationRepository(session).get(context.job.tenant_id, context.job.entity_id)
            if run is not None and run.status not in {"completed", "cancelled"}:
                run.status = "failed"
                run.last_error_code = code
                run.last_error_message = message[:500]
                session.commit()
