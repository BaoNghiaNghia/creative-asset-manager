from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import (
    DeferredJobOutcome,
    JobHandlerContext,
    JobHandlerResult,
)
from app.domain.providers.contracts import (
    OpenStoredAssetInput,
    StorageProviderError,
    StoreAssetInput,
)
from app.modules.ai_operations.credentials import (
    CreativeAiCredentialRepository,
    CreativeCredentialError,
    creative_credential_cipher,
)
from app.modules.image_generation.providers import (
    GeneratedImageResult,
    PreparedImage,
    ReferenceImageInput,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.supervisor import RrugcSupervisorService
from app.providers.ai.gemini_image import (
    GeminiImageProviderError,
    GeminiReferenceImageProvider,
)


MAX_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_REFERENCE_PIXELS = 80_000_000
ALLOWED_PROVIDER_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


class RrugcGenerationHandlerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


async def _bytes_body(content: bytes, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
    for offset in range(0, len(content), chunk_size):
        yield content[offset : offset + chunk_size]


def _output_extension(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime, ".bin")


def _prepared_image(content: bytes) -> PreparedImage:
    if not content or len(content) > MAX_REFERENCE_BYTES:
        raise RrugcGenerationHandlerError(
            "rrugc_generation_reference_invalid",
            "Generation reference image is empty or exceeds the size limit.",
        )
    try:
        with Image.open(BytesIO(content)) as decoded:
            decoded.load()
            oriented = ImageOps.exif_transpose(decoded)
            width, height = oriented.size
            if width < 1 or height < 1 or width * height > MAX_REFERENCE_PIXELS:
                raise RrugcGenerationHandlerError(
                    "rrugc_generation_reference_invalid",
                    "Generation reference image dimensions are invalid.",
                )
            has_alpha = (
                oriented.mode in {"RGBA", "LA"}
                or (oriented.mode == "P" and "transparency" in oriented.info)
            )
            output = BytesIO()
            if has_alpha:
                oriented.convert("RGBA").save(output, "PNG", optimize=True)
                mime = "image/png"
            else:
                oriented.convert("RGB").save(
                    output,
                    "JPEG",
                    quality=95,
                    optimize=True,
                )
                mime = "image/jpeg"
            return PreparedImage(
                image_bytes=output.getvalue(),
                mime_type=mime,
                width=width,
                height=height,
            )
    except RrugcGenerationHandlerError:
        raise
    except Exception as exc:
        raise RrugcGenerationHandlerError(
            "rrugc_generation_reference_invalid",
            "Generation reference image could not be decoded.",
        ) from exc


class RrugcGenerateJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(
        self, context: JobHandlerContext
    ) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except RrugcGenerationHandlerError as exc:
            self._mark_failure(
                context,
                exc.code,
                str(exc),
                terminal=(not exc.retryable or self._retry_exhausted(context)),
            )
            context.logger.warning(
                "rrugc_generation_failed",
                extra={
                    "attempt_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                },
            )
            outcome = (
                JobHandlerResult.retryable
                if exc.retryable
                else JobHandlerResult.non_retryable
            )
            return outcome(exc.code, str(exc))
        except Exception:
            self._mark_failure(
                context,
                "rrugc_generation_internal_error",
                "Realistic Review UGC generation failed.",
                terminal=self._retry_exhausted(context),
            )
            context.logger.exception(
                "rrugc_generation_failed",
                extra={
                    "attempt_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                    "error_code": "rrugc_generation_internal_error",
                },
            )
            return JobHandlerResult.retryable(
                "rrugc_generation_internal_error",
                "Realistic Review UGC generation failed.",
            )

    async def _execute(
        self, context: JobHandlerContext
    ) -> JobHandlerResult | DeferredJobOutcome:
        settings = self.settings or get_settings()
        attempt_id = str(context.job.payload.get("generation_attempt_id") or "")
        if (
            not attempt_id
            or attempt_id != context.job.entity_id
            or context.job.job_type != "rrugc_generate"
        ):
            return JobHandlerResult.non_retryable(
                "rrugc_generation_job_invalid",
                "Generation job payload is invalid.",
            )
        if context.is_cancelled:
            self._mark_failure(
                context,
                "operation_cancelled",
                "Generation was cancelled.",
                terminal=True,
            )
            return JobHandlerResult.cancelled("Generation was cancelled.")
        if not settings.IMAGE_GENERATION_ENABLED or not settings.GEMINI_IMAGE_GENERATION_ENABLED:
            raise RrugcGenerationHandlerError(
                "rrugc_generation_disabled",
                "Reference-conditioned image generation is disabled.",
            )

        storage = context.dependencies.storage_provider
        if storage is None:
            raise RrugcGenerationHandlerError(
                "managed_storage_unavailable",
                "Managed storage is unavailable.",
                retryable=True,
            )

        with context.dependencies.session_factory() as session:
            repository = RrugcRepository(session)
            attempt = repository.lock_generation_attempt(
                context.job.tenant_id, attempt_id
            )
            if attempt is None:
                raise RrugcGenerationHandlerError(
                    "rrugc_generation_attempt_not_found",
                    "Generation attempt was not found.",
                )
            if attempt.status == "completed":
                if attempt.output_remote_file_id:
                    RrugcSupervisorService(session).enqueue(attempt=attempt)
                return JobHandlerResult.completed()
            if attempt.status == "failed":
                return JobHandlerResult.non_retryable(
                    attempt.last_error_code or "rrugc_generation_failed",
                    attempt.last_error_message or "Generation failed.",
                )
            if attempt.status not in {"queued", "running"}:
                raise RrugcGenerationHandlerError(
                    "rrugc_generation_state_invalid",
                    "Generation attempt is not queued.",
                )
            candidate_snapshot = dict(attempt.candidate_snapshot_json or {})
            product_references = list(attempt.product_reference_snapshot_json or [])
            prompt = (attempt.prompt_text or "").strip()
            if not candidate_snapshot.get("remote_file_id") or not product_references:
                raise RrugcGenerationHandlerError(
                    "rrugc_generation_provenance_incomplete",
                    "Generation provenance is incomplete.",
                )
            attempt.status = "running"
            attempt.started_at = attempt.started_at or datetime.now(timezone.utc)
            attempt.last_error_code = None
            attempt.last_error_message = None
            session.commit()

        staged = self._staging_path(settings, attempt_id)
        result = self._load_staged_result(staged, attempt)
        if result is None:
            person = await self._open_prepared(
                storage,
                tenant_id=context.job.tenant_id,
                asset_id=f"rrugc-person:{candidate_snapshot.get('id') or attempt_id}",
                remote_file_id=str(candidate_snapshot["remote_file_id"]),
            )
            references: list[ReferenceImageInput] = []
            for item in product_references[:10]:
                remote_file_id = str(item.get("remote_file_id") or "")
                if not remote_file_id:
                    continue
                references.append(
                    ReferenceImageInput(
                        image=await self._open_prepared(
                            storage,
                            tenant_id=context.job.tenant_id,
                            asset_id=f"rrugc-product-reference:{item.get('id') or item.get('view_type')}",
                            remote_file_id=remote_file_id,
                            content_type=(
                                str(item.get("content_type"))
                                if item.get("content_type")
                                else None
                            ),
                        ),
                        role="product",
                        label=str(item.get("view_type") or "reference"),
                    )
                )
            if not references:
                raise RrugcGenerationHandlerError(
                    "rrugc_generation_product_references_unavailable",
                    "No bound product reference could be loaded.",
                )

            adapter = GeminiReferenceImageProvider(
                api_key=self._gemini_image_key(context, settings)
            )
            try:
                result = await adapter.generate_from_references(
                    person=person,
                    references=references,
                    prompt=prompt,
                )
            except GeminiImageProviderError as exc:
                if exc.code == "gemini_image_rate_limited":
                    self._mark_failure(
                        context,
                        exc.code,
                        str(exc),
                        terminal=False,
                    )
                    return DeferredJobOutcome(
                        "gemini_image_quota_deferred",
                        str(exc),
                        datetime.now(timezone.utc) + timedelta(hours=1),
                    )
                raise RrugcGenerationHandlerError(
                    exc.code,
                    str(exc),
                    retryable=exc.retryable,
                ) from exc
            finally:
                await adapter.aclose()
            self._write_stage(staged, result.image_bytes)

        try:
            with Image.open(BytesIO(result.image_bytes)) as output_image:
                output_image.load()
                output_width, output_height = output_image.size
        except Exception as exc:
            raise RrugcGenerationHandlerError(
                "rrugc_generation_output_invalid",
                "Generated output image is invalid.",
            ) from exc

        content_hash = hashlib.sha256(result.image_bytes).hexdigest()
        with context.dependencies.session_factory() as session:
            staged_attempt = RrugcRepository(session).lock_generation_attempt(
                context.job.tenant_id, attempt_id
            )
            if staged_attempt is None:
                raise RrugcGenerationHandlerError(
                    "rrugc_generation_attempt_not_found",
                    "Generation attempt was not found after provider execution.",
                )
            staged_attempt.provider = result.provider
            staged_attempt.provider_model = result.model
            staged_attempt.provider_request_id = (
                result.provider_request_id or staged_attempt.provider_request_id
            )
            staged_attempt.output_content_hash = content_hash
            staged_attempt.output_content_type = result.mime_type
            staged_attempt.output_size_bytes = len(result.image_bytes)
            staged_attempt.output_width = output_width
            staged_attempt.output_height = output_height
            session.commit()
        try:
            stored = await storage.store_asset(
                StoreAssetInput(
                    tenant_id=context.job.tenant_id,
                    content_hash=content_hash,
                    body=_bytes_body(result.image_bytes),
                    asset_id=f"rrugc-generation:{attempt_id}",
                    content_type=result.mime_type,
                    size_bytes=len(result.image_bytes),
                    filename=(
                        f"rrugc-{attempt_id}{_output_extension(result.mime_type)}"
                    ),
                )
            )
        except StorageProviderError as exc:
            raise RrugcGenerationHandlerError(
                exc.code,
                "Generated image could not be stored in Managed Drive.",
                retryable=exc.retryable,
            ) from exc

        if not stored.remote_file_id:
            raise RrugcGenerationHandlerError(
                "rrugc_generation_storage_invalid",
                "Managed Drive returned no file identifier for the generated image.",
                retryable=True,
            )

        with context.dependencies.session_factory() as session:
            attempt = RrugcRepository(session).lock_generation_attempt(
                context.job.tenant_id, attempt_id
            )
            if attempt is None:
                raise RrugcGenerationHandlerError(
                    "rrugc_generation_attempt_not_found",
                    "Generation attempt was not found after provider execution.",
                )
            attempt.provider = result.provider
            attempt.provider_model = result.model
            attempt.provider_request_id = result.provider_request_id
            attempt.output_content_hash = content_hash
            attempt.output_content_type = result.mime_type
            attempt.output_size_bytes = len(result.image_bytes)
            attempt.output_width = output_width
            attempt.output_height = output_height
            attempt.output_remote_file_id = stored.remote_file_id
            attempt.output_remote_folder_id = stored.remote_folder_id
            attempt.output_web_url = stored.web_url
            attempt.status = "completed"
            attempt.completed_at = datetime.now(timezone.utc)
            attempt.last_error_code = None
            attempt.last_error_message = None
            session.commit()
            RrugcSupervisorService(session).enqueue(attempt=attempt)

        self._remove_stage(staged)
        context.logger.info(
            "rrugc_generation_completed",
            extra={
                "attempt_id": attempt_id,
                "tenant_id": context.job.tenant_id,
                "provider": result.provider,
                "model": result.model,
                "output_width": output_width,
                "output_height": output_height,
            },
        )
        return JobHandlerResult.completed()



    @staticmethod
    def _staging_path(settings: Settings, attempt_id: str) -> Path:
        configured = getattr(
            settings,
            "IMAGE_GENERATION_STAGING_ROOT",
            "/tmp/creative-asset-manager-image-generation",
        )
        root = Path(str(configured)).resolve() / "rrugc"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{attempt_id}.result"

    @staticmethod
    def _write_stage(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)

    @staticmethod
    def _remove_stage(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _load_staged_result(
        self,
        path: Path,
        attempt,
    ) -> GeneratedImageResult | None:
        if not path.exists():
            return None
        try:
            data = path.read_bytes()
            if not data or len(data) > MAX_REFERENCE_BYTES:
                raise ValueError("invalid staged result size")
            with Image.open(BytesIO(data)) as image:
                image.load()
                mime = Image.MIME.get(image.format or "", "").lower()
            if mime not in ALLOWED_PROVIDER_MIME_TYPES:
                raise ValueError("invalid staged result MIME")
        except (OSError, ValueError):
            self._remove_stage(path)
            return None
        return GeneratedImageResult(
            provider="gemini",
            model=attempt.provider_model,
            image_bytes=data,
            mime_type=mime,
            provider_request_id=attempt.provider_request_id,
        )


    async def _open_prepared(
        self,
        storage,
        *,
        tenant_id: str,
        asset_id: str,
        remote_file_id: str,
        content_type: str | None = None,
    ) -> PreparedImage:
        try:
            stream = await storage.open_asset(
                OpenStoredAssetInput(
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    remote_file_id=remote_file_id,
                    content_type=content_type,
                )
            )
        except StorageProviderError as exc:
            raise RrugcGenerationHandlerError(
                exc.code,
                "Generation reference could not be opened from Managed Drive.",
                retryable=exc.retryable,
            ) from exc

        data = bytearray()
        try:
            async for chunk in stream.body:
                data.extend(chunk)
                if len(data) > MAX_REFERENCE_BYTES:
                    raise RrugcGenerationHandlerError(
                        "rrugc_generation_reference_too_large",
                        "Generation reference exceeds the size limit.",
                    )
        except StorageProviderError as exc:
            raise RrugcGenerationHandlerError(
                exc.code,
                "Generation reference could not be read from Managed Drive.",
                retryable=exc.retryable,
            ) from exc
        finally:
            await stream.close()
        return _prepared_image(bytes(data))

    def _gemini_image_key(self, context: JobHandlerContext, settings: Settings) -> str:
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
                        context.job.tenant_id,
                        provider="gemini_image",
                    )
            except CreativeCredentialError as exc:
                raise RrugcGenerationHandlerError(
                    exc.code,
                    "Gemini image credential is unavailable.",
                ) from exc
            if credential is None:
                raise RrugcGenerationHandlerError(
                    "gemini_image_credential_unavailable",
                    "Gemini image credential is unavailable.",
                )
            return credential.secret

        fallback = (settings.GEMINI_IMAGE_API_KEY or "").strip()
        if not fallback:
            raise RrugcGenerationHandlerError(
                "gemini_image_credential_unavailable",
                "Gemini image credential is unavailable.",
            )
        return fallback

    @staticmethod
    def _retry_exhausted(context: JobHandlerContext) -> bool:
        with context.dependencies.session_factory() as session:
            job = session.get(ProcessingJobModel, context.job.id)
            return bool(job is not None and job.attempt_count >= job.max_attempts)

    @staticmethod
    def _mark_failure(
        context: JobHandlerContext,
        code: str,
        message: str,
        *,
        terminal: bool,
    ) -> None:
        attempt_id = str(context.job.payload.get("generation_attempt_id") or "")
        if not attempt_id:
            return
        with context.dependencies.session_factory() as session:
            attempt = RrugcRepository(session).lock_generation_attempt(
                context.job.tenant_id, attempt_id
            )
            if attempt is None or attempt.status == "completed":
                return
            attempt.status = "failed" if terminal else "queued"
            attempt.last_error_code = code[:100]
            attempt.last_error_message = message[:1000]
            session.commit()
