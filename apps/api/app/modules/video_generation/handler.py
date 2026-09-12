from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import AsyncIterator
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.modules.assets.content_resolver import SourceAssetContentResolver, SourceAssetContentUnavailable
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.assets.repository import AssetContentConflictError, AssetRegistryRepository
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.storage.repository import ManagedStorageRepository
from app.modules.storage.service import ManagedAssetStorageService
from app.domain.providers.contracts import StorageProviderError, StoreAssetInput

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

        if state == "submission_unknown" and not gateway_id:
            return self._defer("video_generation_recovery_required", "Video generation requires recovery.", settings)

        client = self._client(context, settings)
        try:
            if state == "storing":
                return await self._import_completed_content(context, settings, run_id, gateway_id, client)
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
            if gateway.status == "completed":
                self._apply_gateway_state(context, run_id, gateway, settings)
                return await self._import_completed_content(context, settings, run_id, gateway.generation_id, client)
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

    async def _import_completed_content(self, context, settings, run_id, gateway_id, client):
        """Import one already-completed gateway artifact; this path never submits."""
        if not gateway_id:
            raise VideoGenerationHandlerError("video_generation_recovery_required", "Video generation requires recovery.")
        provider = context.dependencies.storage_provider
        if provider is None or not settings.MANAGED_ASSET_STORAGE_ENABLED:
            raise VideoGenerationHandlerError("video_generation_storage_unavailable", "Managed Storage is unavailable.", retryable=True)
        stage = self._staging_path(settings, run_id)
        provider_name = getattr(provider, "provider_name", provider.__class__.__name__)
        with context.dependencies.session_factory() as session:
            repository = VideoGenerationRepository(session)
            run = repository.get(context.job.tenant_id, run_id)
            if run is None:
                raise VideoGenerationHandlerError("video_generation_not_found", "Video generation was not found.")
            if run.status == "completed":
                return JobHandlerResult.completed()
            if run.output_asset_id:
                stored = session.scalar(select(AssetStorageObjectModel).where(
                    AssetStorageObjectModel.tenant_id == run.tenant_id,
                    AssetStorageObjectModel.asset_id == run.output_asset_id,
                    AssetStorageObjectModel.storage_provider == provider_name,
                    AssetStorageObjectModel.status == "stored",
                ))
                if stored is not None:
                    repository.transition(run, "completed")
                    session.commit()
                    self._remove_stage(stage)
                    return JobHandlerResult.completed()

        content_hash, size_bytes, mime = self._valid_stage(stage)
        if content_hash is None:
            try:
                gateway = await client.get_generation(gateway_id)
                if gateway.status == "failed":
                    return self._apply_gateway_state(context, run_id, gateway, settings)
                if gateway.status == "submission_unknown":
                    return self._defer("upstream_submission_state_unknown", "Video generation submission state is unknown.", settings)
                if gateway.status != "completed":
                    return self._defer("video_generation_provider_running", "Video generation is running.", settings)
                content_hash, size_bytes, mime = await self._download_stage(client, gateway_id, stage, int(settings.VIDEO_GENERATION_MAX_OUTPUT_BYTES))
            except DolaGatewayError as exc:
                code = "dola_generation_content_not_found" if exc.code == "dola_generation_not_found" else exc.code
                raise VideoGenerationHandlerError(code, str(exc), retryable=exc.retryable) from exc

        with context.dependencies.session_factory() as session:
            repository = VideoGenerationRepository(session)
            run = repository.get(context.job.tenant_id, run_id)
            if run is None or run.status != "storing":
                raise VideoGenerationHandlerError("video_generation_state_conflict", "Video generation state conflicts.")
            assets = AssetRegistryRepository(session)
            asset = assets.find_asset_by_content_hash(run.tenant_id, content_hash)
            if asset is None:
                try:
                    asset = assets.create_asset(tenant_id=run.tenant_id, content_hash=content_hash, mime_type=mime, size_bytes=size_bytes)
                except AssetContentConflictError:
                    asset = assets.find_asset_by_content_hash(run.tenant_id, content_hash)
                    if asset is None:
                        raise
            if run.output_asset_id and run.output_asset_id != asset.id:
                raise VideoGenerationHandlerError("video_generation_output_asset_conflict", "Generated video output identity conflicts.")
            run.output_asset_id = asset.id
            session.commit()
            asset_id = asset.id

        try:
            with context.dependencies.session_factory() as session:
                await ManagedAssetStorageService(
                    AssetRegistryRepository(session), ManagedStorageRepository(session),
                    enabled=True, storage_class="durable",
                ).store(StoreAssetInput(
                    tenant_id=context.job.tenant_id, asset_id=asset_id, content_hash=content_hash,
                    body=self._file_chunks(stage), content_type=mime, size_bytes=size_bytes,
                    filename=f"generated-{run_id}.mp4",
                ), provider)
        except StorageProviderError as exc:
            raise VideoGenerationHandlerError("video_generation_storage_failed", "Generated video storage failed.", retryable=exc.retryable) from exc
        except Exception as exc:
            raise VideoGenerationHandlerError("video_generation_storage_failed", "Generated video storage failed.", retryable=True) from exc

        with context.dependencies.session_factory() as session:
            repository = VideoGenerationRepository(session)
            run = repository.get(context.job.tenant_id, run_id)
            if run is None or run.output_asset_id != asset_id:
                raise VideoGenerationHandlerError("video_generation_output_asset_conflict", "Generated video output identity conflicts.")
            if run.status == "storing":
                repository.transition(run, "completed")
                session.commit()
        self._remove_stage(stage)
        context.logger.info("video_generation_completed", extra={"generation_id": run_id, "tenant_id": context.job.tenant_id, "gateway_generation_id": gateway_id, "output_asset_id": asset_id, "size_bytes": size_bytes, "mime_type": mime, "status": "completed"})
        return JobHandlerResult.completed()

    @staticmethod
    def _staging_path(settings, run_id: str) -> Path:
        root = Path(settings.VIDEO_GENERATION_STAGING_ROOT).resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = (root / f"{run_id}.mp4").resolve()
        if path.parent != root:
            raise VideoGenerationHandlerError("invalid_video_generation_job", "Invalid generation identifier.")
        return path

    @staticmethod
    async def _file_chunks(path: Path, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        with path.open("rb") as source:
            while block := source.read(chunk_size):
                yield block

    @staticmethod
    def _valid_stage(path: Path):
        if not path.is_file():
            return None, None, None
        digest = hashlib.sha256(); total = 0; initial = b""
        with path.open("rb") as source:
            while block := source.read(64 * 1024):
                if len(initial) < 4096:
                    initial += block[:4096-len(initial)]
                digest.update(block); total += len(block)
        if total <= 0 or b"ftyp" not in initial[:4096]:
            VideoGenerateJobHandler._remove_stage(path)
            return None, None, None
        return digest.hexdigest(), total, "video/mp4"

    @staticmethod
    async def _download_stage(client, gateway_id: str, path: Path, maximum_bytes: int):
        temporary = path.with_suffix(".tmp")
        digest = hashlib.sha256(); total = 0; initial = b""
        try:
            async with client.open_content(gateway_id, maximum_bytes=maximum_bytes) as content:
                with temporary.open("wb") as target:
                    for_mode = 0o600
                    os.chmod(temporary, for_mode)
                    async for block in content.body:
                        total += len(block)
                        if total > maximum_bytes:
                            raise VideoGenerationHandlerError("video_generation_output_too_large", "Generated video exceeds the configured output limit.")
                        if len(initial) < 4096:
                            initial += block[:4096-len(initial)]
                        digest.update(block); target.write(block)
                    target.flush(); os.fsync(target.fileno())
            if total <= 0 or b"ftyp" not in initial[:4096]:
                raise VideoGenerationHandlerError("dola_generation_content_invalid", "Generated video content is invalid.")
            os.replace(temporary, path)
            return digest.hexdigest(), total, "video/mp4"
        except Exception:
            try: temporary.unlink()
            except FileNotFoundError: pass
            raise

    @staticmethod
    def _remove_stage(path: Path) -> None:
        for candidate in (path, path.with_suffix(".tmp")):
            try: candidate.unlink()
            except FileNotFoundError: pass

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
