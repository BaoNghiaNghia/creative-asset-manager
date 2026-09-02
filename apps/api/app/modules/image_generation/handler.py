from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import AsyncIterator

from PIL import Image, ImageOps
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import StorageProviderError, StoreAssetInput
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository, CreativeCredentialError, creative_credential_cipher
from app.modules.assets.content_resolver import SourceAssetContentResolver, SourceAssetContentUnavailable
from app.modules.assets.model import SourceAssetModel
from app.modules.assets.repository import AssetContentConflictError, AssetRegistryRepository
from app.modules.image_generation.geometry import calculate_square_geometry
from app.modules.image_generation.providers import DeferredGenerationResult, GeneratedImageResult, PreparedImage
from app.modules.image_generation.repository import ImageGenerationRepository
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.storage.repository import ManagedStorageRepository
from app.modules.storage.service import ManagedAssetStorageService
from app.providers.ai.adobe_firefly import AdobeFireflySquareProvider, FireflyProviderError
from app.providers.ai.gemini_image import GeminiImageProviderError, GeminiSquareImageProvider

MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_SOURCE_PIXELS = 80_000_000


class ImageGenerationHandlerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


async def _file_chunks(path: Path, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            yield chunk


def _extension(mime: str) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime]


class ImageGenerateJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except ImageGenerationHandlerError as exc:
            if not exc.retryable:
                self._mark_failed(context, exc.code, str(exc))
            context.logger.warning(
                "image_generation_failed",
                extra={
                    "generation_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                    "provider": context.job.provider_key,
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                },
            )
            outcome = JobHandlerResult.retryable if exc.retryable else JobHandlerResult.non_retryable
            return outcome(exc.code, str(exc))
        except Exception:
            self._mark_failed(context, "image_generation_internal_error", "Image generation failed.")
            context.logger.exception("image_generation_failed", extra={
                "generation_id": context.job.entity_id,
                "error_code": "image_generation_internal_error",
            })
            return JobHandlerResult.non_retryable("image_generation_internal_error", "Image generation failed.")

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        settings = self.settings or get_settings()
        if not settings.IMAGE_GENERATION_ENABLED:
            raise ImageGenerationHandlerError("image_generation_disabled", "Image generation is disabled.")
        run_id = context.job.payload.get("image_generation_run_id")
        if not isinstance(run_id, str) or run_id != context.job.entity_id:
            raise ImageGenerationHandlerError("invalid_image_generation_job", "Image generation job payload is invalid.")
        with context.dependencies.session_factory() as session:
            run = ImageGenerationRepository(session).get(context.job.tenant_id, run_id)
            if run is None:
                raise ImageGenerationHandlerError("image_generation_not_found", "Image generation was not found.")
            if run.status == "completed":
                return JobHandlerResult.completed()
            if run.status == "failed":
                return JobHandlerResult.non_retryable(
                    run.last_error_code or "image_generation_failed",
                    run.last_error_message or "Image generation failed.",
                )
            if run.status == "cancelled" or context.is_cancelled:
                context.logger.info(
                    "image_generation_cancelled",
                    extra={
                        "generation_id": run_id,
                        "tenant_id": context.job.tenant_id,
                        "provider": run.provider,
                        "status": "cancelled",
                    },
                )
                return JobHandlerResult.cancelled("Image generation was cancelled.")
            state, provider, target = run.status, run.provider, run.target_width
            provider_job_id, status_url = run.provider_job_id, run.provider_status_url
        context.logger.info(
            "image_generation_started",
            extra={
                "generation_id": run_id,
                "tenant_id": context.job.tenant_id,
                "provider": provider,
                "target_size": target,
                "status": state,
            },
        )
        staged = self._staging_path(settings, run_id)
        if state == "storing":
            return await self._store(context, settings, staged)
        if provider == "adobe_firefly" and provider_job_id:
            return await self._resume_firefly(context, settings, status_url, staged)

        source = await self._prepare_source(context, run_id)
        if source.width == source.height:
            result = self._local_square(source, target)
            self._write_stage(staged, result.image_bytes)
            with context.dependencies.session_factory() as session:
                repository = ImageGenerationRepository(session)
                run = repository.get_for_update(context.job.tenant_id, run_id)
                if run is None or run.status == "cancelled":
                    return JobHandlerResult.cancelled("Image generation was cancelled.")
                run.provider_model = "local-square-normalize"
                repository.transition(run, "storing")
                session.commit()
            return await self._store(context, settings, staged)

        if provider == "adobe_firefly":
            return await self._submit_firefly(context, settings, source, target)
        if provider == "gemini":
            return await self._run_gemini(context, settings, source, target, staged)
        raise ImageGenerationHandlerError("image_generation_provider_invalid", "Image generation provider is invalid.")

    async def _submit_firefly(self, context, settings, source, target):
        if not settings.FIREFLY_IMAGE_GENERATION_ENABLED:
            raise ImageGenerationHandlerError("firefly_not_configured", "Adobe Firefly is unavailable.")
        adapter = AdobeFireflySquareProvider(
            client_id=settings.FIREFLY_SERVICES_CLIENT_ID,
            client_secret=settings.FIREFLY_SERVICES_CLIENT_SECRET,
        )
        try:
            submitted = await adapter.generate_square(
                source=source, target_size=target, prompt=self._prompt(context)
            )
        except FireflyProviderError as exc:
            raise ImageGenerationHandlerError(exc.code, str(exc), retryable=exc.retryable) from exc
        finally:
            await adapter.aclose()
        assert isinstance(submitted, DeferredGenerationResult)
        with context.dependencies.session_factory() as session:
            repository = ImageGenerationRepository(session)
            run = repository.get_for_update(context.job.tenant_id, context.job.entity_id)
            if run is None or run.status == "cancelled":
                return JobHandlerResult.cancelled("Image generation was cancelled.")
            run.provider_upload_id = submitted.upload_id
            run.provider_job_id = submitted.provider_job_id
            run.provider_status_url = submitted.status_url
            run.provider_cancel_url = submitted.cancel_url
            repository.transition(run, "submitted")
            session.commit()
        context.logger.info("image_generation_provider_submitted", extra={
            "generation_id": context.job.entity_id,
            "provider": "adobe_firefly",
            "job_id": submitted.provider_job_id,
        })
        return DeferredJobOutcome(
            "image_generation_provider_running",
            "Adobe Firefly generation is running.",
            datetime.now(timezone.utc) + timedelta(seconds=10),
        )

    async def _run_gemini(self, context, settings, source, target, staged):
        if not settings.GEMINI_IMAGE_GENERATION_ENABLED:
            raise ImageGenerationHandlerError("gemini_image_not_configured", "Gemini image generation is unavailable.")
        adapter = GeminiSquareImageProvider(api_key=self._gemini_image_key(context, settings))
        try:
            result = await adapter.generate_square(
                source=source, target_size=target, prompt=self._prompt(context)
            )
        except GeminiImageProviderError as exc:
            if exc.code == "gemini_image_rate_limited":
                return DeferredJobOutcome(
                    "gemini_image_quota_deferred",
                    str(exc),
                    datetime.now(timezone.utc) + timedelta(hours=1),
                )
            raise ImageGenerationHandlerError(exc.code, str(exc), retryable=exc.retryable) from exc
        finally:
            await adapter.aclose()
        self._write_stage(staged, result.image_bytes)
        with context.dependencies.session_factory() as session:
            repository = ImageGenerationRepository(session)
            run = repository.get_for_update(context.job.tenant_id, context.job.entity_id)
            if run is None or run.status == "cancelled":
                return JobHandlerResult.cancelled("Image generation was cancelled.")
            run.provider_interaction_id = result.provider_request_id
            repository.transition(run, "storing")
            session.commit()
        return await self._store(context, settings, staged)

    async def _prepare_source(self, context: JobHandlerContext, run_id: str) -> PreparedImage:
        with context.dependencies.session_factory() as session:
            repository = ImageGenerationRepository(session)
            run = repository.get_for_update(context.job.tenant_id, run_id)
            if run is None or not run.source_source_asset_id:
                raise ImageGenerationHandlerError("source_asset_unavailable", "Source image is unavailable.")
            if run.status == "queued":
                repository.transition(run, "preparing")
                session.commit()
            source_id, target = run.source_source_asset_id, run.target_width
        resolver = SourceAssetContentResolver(context.dependencies.session_factory)
        data = bytearray()
        try:
            async with resolver.open(
                tenant_id=context.job.tenant_id, source_asset_id=source_id
            ) as stream:
                async for chunk in stream.body:
                    data.extend(chunk)
                    if len(data) > MAX_SOURCE_BYTES:
                        raise ImageGenerationHandlerError(
                            "source_image_too_large", "Source image exceeds the size limit."
                        )
                declared_mime = stream.content_type.split(";", 1)[0].lower()
        except SourceAssetContentUnavailable as exc:
            raise ImageGenerationHandlerError(
                "source_asset_unavailable", "Source image is unavailable."
            ) from exc
        try:
            with Image.open(BytesIO(bytes(data))) as decoded:
                decoded.load()
                oriented = ImageOps.exif_transpose(decoded)
                if oriented.width * oriented.height > MAX_SOURCE_PIXELS:
                    raise ImageGenerationHandlerError(
                        "source_image_too_large", "Source image dimensions exceed the limit."
                    )
                mime = Image.MIME.get(decoded.format or "", "").lower()
                allowed = {"image/jpeg", "image/png", "image/webp"}
                if mime not in allowed or declared_mime not in allowed or mime != declared_mime:
                    raise ImageGenerationHandlerError(
                        "source_image_unsupported", "Source image MIME is unsupported or mismatched."
                    )
                output = BytesIO()
                save_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[mime]
                if save_format == "JPEG":
                    oriented = oriented.convert("RGB")
                oriented.save(output, save_format, quality=95)
                prepared = PreparedImage(
                    image_bytes=output.getvalue(),
                    mime_type=mime,
                    width=oriented.width,
                    height=oriented.height,
                )
        except ImageGenerationHandlerError:
            raise
        except Exception as exc:
            raise ImageGenerationHandlerError(
                "source_image_unsupported", "Source image could not be decoded."
            ) from exc
        geometry = calculate_square_geometry(prepared.width, prepared.height, target)
        with context.dependencies.session_factory() as session:
            run = ImageGenerationRepository(session).get_for_update(
                context.job.tenant_id, run_id
            )
            if run is None:
                raise ImageGenerationHandlerError(
                    "image_generation_not_found", "Image generation was not found."
                )
            run.source_width, run.source_height = geometry.source_width, geometry.source_height
            run.normalized_width, run.normalized_height = geometry.normalized_width, geometry.normalized_height
            run.left, run.top, run.right, run.bottom = (
                geometry.left, geometry.top, geometry.right, geometry.bottom
            )
            session.commit()
        return prepared

    async def _resume_firefly(self, context, settings, status_url, staged):
        if not status_url:
            raise ImageGenerationHandlerError(
                "firefly_invalid_response", "Adobe Firefly job has no status URL."
            )
        adapter = AdobeFireflySquareProvider(
            client_id=settings.FIREFLY_SERVICES_CLIENT_ID,
            client_secret=settings.FIREFLY_SERVICES_CLIENT_SECRET,
        )
        try:
            polled = await adapter.poll(status_url=status_url)
            if polled.state == "running":
                context.logger.info(
                    "image_generation_provider_running",
                    extra={
                        "generation_id": context.job.entity_id,
                        "tenant_id": context.job.tenant_id,
                        "provider": "adobe_firefly",
                        "status": "running",
                    },
                )
                with context.dependencies.session_factory() as session:
                    repository = ImageGenerationRepository(session)
                    run = repository.get_for_update(
                        context.job.tenant_id, context.job.entity_id
                    )
                    if run is not None and run.status == "submitted":
                        repository.transition(run, "running")
                        session.commit()
                return DeferredJobOutcome(
                    "image_generation_provider_running",
                    "Adobe Firefly generation is running.",
                    datetime.now(timezone.utc)
                    + timedelta(seconds=max(10, min(60, polled.retry_after_seconds))),
                )
            if polled.state == "cancelled":
                with context.dependencies.session_factory() as session:
                    repository = ImageGenerationRepository(session)
                    run = repository.get_for_update(
                        context.job.tenant_id, context.job.entity_id
                    )
                    if run is not None and run.status != "cancelled":
                        repository.cancel(run)
                        session.commit()
                return JobHandlerResult.cancelled("Adobe Firefly generation was cancelled.")
            if polled.state == "failed" or not polled.output_url:
                raise ImageGenerationHandlerError(
                    polled.error_code or "firefly_generation_failed",
                    "Adobe Firefly generation failed.",
                )
            with context.dependencies.session_factory() as session:
                run = ImageGenerationRepository(session).get(
                    context.job.tenant_id, context.job.entity_id
                )
                if run is None:
                    raise ImageGenerationHandlerError(
                        "image_generation_not_found", "Image generation was not found."
                    )
                target = run.target_width
            result = await adapter.download_result(
                output_url=polled.output_url, target_size=target
            )
        except FireflyProviderError as exc:
            raise ImageGenerationHandlerError(
                exc.code, str(exc), retryable=exc.retryable
            ) from exc
        finally:
            await adapter.aclose()
        self._write_stage(staged, result.image_bytes)
        with context.dependencies.session_factory() as session:
            repository = ImageGenerationRepository(session)
            run = repository.get_for_update(
                context.job.tenant_id, context.job.entity_id
            )
            if run is None or run.status == "cancelled":
                return JobHandlerResult.cancelled("Image generation was cancelled.")
            if run.status == "submitted":
                repository.transition(run, "running")
            repository.transition(run, "storing")
            session.commit()
        return await self._store(context, settings, staged)

    async def _store(self, context, settings, staged: Path) -> JobHandlerResult:
        context.logger.info(
            "image_generation_storing",
            extra={
                "generation_id": context.job.entity_id,
                "tenant_id": context.job.tenant_id,
                "provider": context.job.provider_key,
                "status": "storing",
            },
        )
        provider = context.dependencies.storage_provider
        if provider is None or not settings.MANAGED_ASSET_STORAGE_ENABLED:
            raise ImageGenerationHandlerError(
                "image_generation_storage_unavailable",
                "Managed Storage is unavailable.",
                retryable=True,
            )
        with context.dependencies.session_factory() as session:
            repository = ImageGenerationRepository(session)
            run = repository.get_for_update(
                context.job.tenant_id, context.job.entity_id
            )
            if run is None:
                raise ImageGenerationHandlerError(
                    "image_generation_not_found", "Image generation was not found."
                )
            if run.status == "cancelled":
                return JobHandlerResult.cancelled("Image generation was cancelled.")
            provider_name = getattr(
                provider, "provider_name", provider.__class__.__name__
            )
            if run.output_asset_id:
                stored = session.scalar(
                    select(AssetStorageObjectModel).where(
                        AssetStorageObjectModel.tenant_id == run.tenant_id,
                        AssetStorageObjectModel.asset_id == run.output_asset_id,
                        AssetStorageObjectModel.storage_provider == provider_name,
                        AssetStorageObjectModel.status == "stored",
                    )
                )
                if stored is not None:
                    repository.transition(run, "completed")
                    session.commit()
                    self._remove_stage(staged)
                    return JobHandlerResult.completed()
            if not staged.is_file():
                raise ImageGenerationHandlerError(
                    "image_generation_stage_missing",
                    "Generated image staging data is unavailable; provider will not be resubmitted.",
                )
            data = staged.read_bytes()
            content_hash = hashlib.sha256(data).hexdigest()
            assets = AssetRegistryRepository(session)
            asset = assets.find_asset_by_content_hash(run.tenant_id, content_hash)
            if asset is None:
                try:
                    asset = assets.create_asset(
                        tenant_id=run.tenant_id,
                        content_hash=content_hash,
                        mime_type=self._staged_mime(data),
                        size_bytes=len(data),
                    )
                except AssetContentConflictError:
                    asset = assets.find_asset_by_content_hash(
                        run.tenant_id, content_hash
                    )
                    if asset is None:
                        raise
            run.output_asset_id = asset.id
            session.commit()
            asset_id = asset.id
            mime = asset.mime_type or "image/png"
            filename = self._filename(session, run, mime)

        try:
            with context.dependencies.session_factory() as session:
                await ManagedAssetStorageService(
                    AssetRegistryRepository(session),
                    ManagedStorageRepository(session),
                    enabled=True,
                ).store(
                    StoreAssetInput(
                        tenant_id=context.job.tenant_id,
                        content_hash=content_hash,
                        body=_file_chunks(staged),
                        asset_id=asset_id,
                        content_type=mime,
                        size_bytes=staged.stat().st_size,
                        filename=filename,
                    ),
                    provider,
                )
        except StorageProviderError as exc:
            raise ImageGenerationHandlerError(
                "image_generation_storage_failed",
                "Generated image storage failed.",
                retryable=exc.retryable,
            ) from exc
        except Exception as exc:
            raise ImageGenerationHandlerError(
                "image_generation_storage_failed",
                "Generated image storage failed.",
                retryable=True,
            ) from exc
        with context.dependencies.session_factory() as session:
            repository = ImageGenerationRepository(session)
            run = repository.get_for_update(
                context.job.tenant_id, context.job.entity_id
            )
            if run is not None and run.status != "cancelled":
                repository.transition(run, "completed")
                session.commit()
        self._remove_stage(staged)
        context.logger.info("image_generation_completed", extra={
            "generation_id": context.job.entity_id,
            "tenant_id": context.job.tenant_id,
            "provider": context.job.provider_key,
        })
        return JobHandlerResult.completed()

    def _gemini_image_key(self, context, settings) -> str:
        with context.dependencies.session_factory() as session:
            metadata = CreativeAiCredentialRepository(session, None).get_metadata(
                context.job.tenant_id, provider="gemini_image"
            )
        if metadata is not None and metadata.status == "active":
            try:
                cipher = creative_credential_cipher(settings)
                with context.dependencies.session_factory() as session:
                    credential = CreativeAiCredentialRepository(
                        session, cipher
                    ).get_active_secret(
                        context.job.tenant_id, provider="gemini_image"
                    )
            except CreativeCredentialError as exc:
                raise ImageGenerationHandlerError(
                    exc.code, "Gemini image credential is unavailable."
                ) from exc
            if credential is None:
                raise ImageGenerationHandlerError(
                    "gemini_image_credential_unavailable",
                    "Gemini image credential is unavailable.",
                )
            return credential.secret
        fallback = (settings.GEMINI_IMAGE_API_KEY or "").strip()
        if not fallback:
            raise ImageGenerationHandlerError(
                "gemini_image_credential_unavailable",
                "Gemini image credential is unavailable.",
            )
        return fallback

    def _prompt(self, context) -> str | None:
        with context.dependencies.session_factory() as session:
            run = ImageGenerationRepository(session).get(
                context.job.tenant_id, context.job.entity_id
            )
            return run.prompt if run else None

    def _mark_failed(self, context, code: str, message: str) -> None:
        with context.dependencies.session_factory() as session:
            repository = ImageGenerationRepository(session)
            run = repository.get(
                context.job.tenant_id, context.job.entity_id
            )
            if run is not None:
                repository.fail(run, code, message)
                session.commit()

    @staticmethod
    def _local_square(source: PreparedImage, target: int) -> GeneratedImageResult:
        with Image.open(BytesIO(source.image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            resized = image.resize((target, target), Image.Resampling.LANCZOS)
            output = BytesIO()
            if image.mode in {"RGBA", "LA"}:
                resized.save(output, "PNG")
                mime = "image/png"
            else:
                resized.convert("RGB").save(output, "JPEG", quality=95)
                mime = "image/jpeg"
        return GeneratedImageResult(
            provider="gemini",
            model="local-square-normalize",
            image_bytes=output.getvalue(),
            mime_type=mime,
        )

    @staticmethod
    def _staged_mime(data: bytes) -> str:
        try:
            with Image.open(BytesIO(data)) as image:
                image.load()
                mime = Image.MIME.get(image.format or "", "").lower()
        except Exception as exc:
            raise ImageGenerationHandlerError(
                "provider_result_invalid_image", "Generated image is invalid."
            ) from exc
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise ImageGenerationHandlerError(
                "provider_result_invalid_image", "Generated image is invalid."
            )
        return mime

    @staticmethod
    def _staging_path(settings: Settings, run_id: str) -> Path:
        root = Path(settings.IMAGE_GENERATION_STAGING_ROOT).resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = (root / f"{run_id}.image").resolve()
        if path.parent != root:
            raise ImageGenerationHandlerError(
                "invalid_image_generation_job", "Invalid generation identifier."
            )
        return path

    @staticmethod
    def _write_stage(path: Path, data: bytes) -> None:
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    @staticmethod
    def _remove_stage(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _filename(session, run, mime: str) -> str:
        source = (
            session.get(SourceAssetModel, run.source_source_asset_id)
            if run.source_source_asset_id
            else None
        )
        stem = Path(source.filename or "generated").stem if source else "generated"
        return f"{stem}-square-{run.target_width}.{_extension(mime)}"
