"""Stage 2 (UI): independent embroidery_ -> 13 official 8869 stock colorways.

The Stage 4 Pinterest/image-generation pipeline uses its legacy 'stage2' module.
These are separate workflows and must never share output/job identities.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from io import BytesIO
from uuid import uuid4

from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import OpenStoredAssetInput, StoreAssetInput, StorageProviderError
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.generation_handler import (
    RrugcGenerationHandlerError, _bytes_body, _output_extension, _prepared_image,
    MAX_REFERENCE_BYTES, MAX_REFERENCE_PIXELS,
)
from app.modules.image_generation.providers import ReferenceImageInput
from app.modules.realistic_review_ugc.model import RrugcColorwayJobModel, RrugcSourcePlanModel
from app.modules.realistic_review_ugc.skill_registry import ensure_skill_registry, assert_skill_enabled
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillRegistryError, resolve_stage2_skill, verify_stage2_skill_runtime,
    installed_stage2_skill_sha256,
)
from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError

JOB_TYPE = "rrugc_colorway_generate"
STOCK_SKILL = "gatorhats-8869-image-studio"
DEFAULT_COLORWAY_SKILL = "gatorhats-8869-scale-image"
# Same order/labels as STOCK_MANIFEST.md. These are the 13 real stock colors, not arbitrary numbers.
COLORS = (
    ("khaki-maroon", "Khaki/Maroon"),
    ("natural-black", "Natural/Black"),
    ("natural-brown", "Natural/Brown"),
    ("natural-camo-green", "Natural/Camo Green"),
    ("natural-charcoal", "Natural/Charcoal"),
    ("natural-forest-green", "Natural/Forest Green"),
    ("natural-khaki", "Natural/Khaki"),
    ("natural-maroon", "Natural/Maroon"),
    ("natural-mossy-oak-breakup", "Natural/Mossy Oak Breakup"),
    ("natural-navy", "Natural/Navy"),
    ("natural-realtree-all-purpose", "Natural/Realtree All Purpose"),
    ("natural-red", "Natural/Red"),
    ("natural-royal", "Natural/Royal"),
)
MAX_MANUAL_RETRIES = 3


class ColorwayError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _stock_path(settings: Settings, color_key: str) -> Path:
    if color_key not in dict(COLORS):
        raise ColorwayError("colorway_unknown_color", "Unknown official stock color.", 422)
    # Only read installed, administrator-managed bundled stock. No client-provided paths/URLs.
    return Path(str(getattr(settings, "CODEX_IMAGE_HOME", "/var/lib/creative-asset-manager/codex"))).resolve() / (
        "skills/" + STOCK_SKILL + "/assets/stock/" + color_key + "/front.jpg"
    )


def _stock_bytes(settings: Settings, color_key: str) -> bytes:
    path = _stock_path(settings, color_key)
    if not path.is_file():
        raise ColorwayError("colorway_stock_missing",
                            "The official 13-color stock images must be installed before running Stage 2.", 503)
    if path.stat().st_size > MAX_REFERENCE_BYTES:
        raise ColorwayError("colorway_stock_invalid", "An official stock photo exceeds the size limit.", 503)
    data = path.read_bytes()
    # Require an image, and enforce the same bounded decoder as other RRUGC inputs.
    try:
        _prepared_image(data)
    except RrugcGenerationHandlerError as exc:
        raise ColorwayError("colorway_stock_invalid", "An official stock photo is invalid.", 503) from exc
    return data


def effective_status(row: RrugcColorwayJobModel, processing: ProcessingJobModel | None) -> str:
    if row.status == "completed":
        return "completed"
    if processing and processing.status in ("failed", "completed"):
        return "failed"
    if processing and processing.status == "processing":
        return "running"
    return row.status


def _codex_runtime_available(settings: Settings) -> bool:
    """Cheap readiness gate. Provider login/quota still require a live smoke."""
    binary = str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex")
    home = Path(str(getattr(settings, "CODEX_IMAGE_HOME",
                            "/var/lib/creative-asset-manager/codex"))).resolve()
    return bool(
        shutil.which(binary)
        and (home / "skills" / DEFAULT_COLORWAY_SKILL / "SKILL.md").is_file()
    )


class ColorwayService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def readiness(self) -> dict:
        """Read-only preflight: avoid misleading users into queueing jobs that cannot run."""
        configured = all((
            self.settings.PROCESSING_JOBS_ENABLED,
            self.settings.IMAGE_GENERATION_ENABLED,
            self.settings.MANAGED_ASSET_STORAGE_ENABLED,
            self.settings.CODEX_IMAGE_GENERATION_ENABLED,
        ))
        available_colors = 0
        for color_key, _ in COLORS:
            try:
                _stock_bytes(self.settings, color_key)
                available_colors += 1
            except (ColorwayError, OSError):
                continue
        error_code = (
            "colorway_disabled" if not configured else
            "colorway_stock_missing" if available_colors != len(COLORS) else
            "colorway_runtime_missing" if not _codex_runtime_available(self.settings) else None
        )
        return {
            "ready": error_code is None,
            "error_code": error_code,
            "stock_ready_count": available_colors,
            "stock_total_count": len(COLORS),
        }

    def _row(self, tenant_id: str, job_id: str) -> RrugcColorwayJobModel | None:
        return self.session.scalar(select(RrugcColorwayJobModel).where(
            RrugcColorwayJobModel.tenant_id == tenant_id, RrugcColorwayJobModel.id == job_id,
        ).with_for_update())

    def _enqueue(self, row: RrugcColorwayJobModel) -> None:
        processing = ProcessingRepository(self.session, self.settings).create_job(
            tenant_id=row.tenant_id, job_type=JOB_TYPE, entity_type="rrugc_colorway_job",
            entity_id=row.id, idempotency_key=f"rrugc-colorway:{row.id}:{row.retry_count}",
            payload={"colorway_job_id": row.id}, priority=20, max_attempts=3,
            provider_key="codex", provider_scope="ai",
        )
        row.processing_job_id = processing.id

    def queue(self, *, tenant_id: str, user_id: str, source_plan_ids: list[str],
              skill_source: str | None = None, skill_id: str | None = None,
              skill_name: str | None = None, skill_version: str | None = None) -> dict:
        ids = list(dict.fromkeys(source_plan_ids))
        if not ids or len(ids) > 50:
            raise ColorwayError("colorway_batch_size", "Choose between 1 and 50 designs per request.", 422)
        if not all((
            self.settings.PROCESSING_JOBS_ENABLED,
            self.settings.IMAGE_GENERATION_ENABLED,
            self.settings.MANAGED_ASSET_STORAGE_ENABLED,
            self.settings.CODEX_IMAGE_GENERATION_ENABLED,
        )):
            raise ColorwayError(
                "colorway_disabled",
                "Stage 2 colorway generation is not enabled on the server.",
                503,
            )
        try:
            skill = resolve_stage2_skill(
                settings=self.settings, skill_source=skill_source, skill_id=skill_id,
                skill_name=skill_name, skill_version=skill_version,
                fallback_skill_name=DEFAULT_COLORWAY_SKILL,
            )
            ensure_skill_registry(self.session, tenant_id=tenant_id)
            assert_skill_enabled(self.session, tenant_id=tenant_id, source=skill.source,
                                 skill_id=skill.skill_id, skill_name=skill.skill_name)
        except Stage2SkillRegistryError as exc:
            raise ColorwayError(exc.code, exc.message, exc.status_code) from exc

        # Validate all 13 stock photos *before* committing any jobs or spending provider credits.
        stock_digests = {key: hashlib.sha256(_stock_bytes(self.settings, key)).hexdigest() for key, _ in COLORS}
        plans = list(self.session.scalars(select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == tenant_id, RrugcSourcePlanModel.id.in_(ids),
        ).order_by(RrugcSourcePlanModel.id).with_for_update()))
        if len(plans) != len(ids) or any(
            p.status == "missing" or not p.source_name.casefold().startswith("embroidery_") for p in plans
        ):
            raise ColorwayError("colorway_source_invalid",
                                "Only available embroidery_ source files in this tenant can be generated.", 422)
        existing = {(j.source_plan_id, j.source_revision, j.color_key) for j in self.session.scalars(
            select(RrugcColorwayJobModel).where(
                RrugcColorwayJobModel.tenant_id == tenant_id,
                RrugcColorwayJobModel.source_plan_id.in_(ids),
            )
        )}
        count = 0
        for plan in plans:
            for color_key, _ in COLORS:
                if (plan.id, plan.source_revision, color_key) in existing:
                    continue
                row = RrugcColorwayJobModel(
                    id=str(uuid4()), tenant_id=tenant_id, source_plan_id=plan.id,
                    source_revision=plan.source_revision, color_key=color_key,
                    stock_sha256=stock_digests[color_key],
                    skill_source=skill.source, skill_id=skill.skill_id,
                    skill_name=skill.skill_name, skill_version=skill.skill_version,
                    skill_bundle_sha256=installed_stage2_skill_sha256(skill.skill_name, settings=self.settings),
                    created_by_user_id=user_id, status="queued",
                )
                self.session.add(row)
                self.session.flush()
                self._enqueue(row)
                count += 1
        self.session.commit()
        return {"queued": count, "existing": len(ids) * len(COLORS) - count}

    def retry(self, *, tenant_id: str, job_id: str) -> RrugcColorwayJobModel:
        row = self._row(tenant_id, job_id)
        if row is None:
            raise ColorwayError("colorway_not_found", "Colorway job not found.", 404)
        processing = self.session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
        if effective_status(row, processing) != "failed":
            raise ColorwayError("colorway_retry_not_allowed", "Only failed colorways can be retried.")
        if row.retry_count >= MAX_MANUAL_RETRIES:
            raise ColorwayError("colorway_retry_limit", "Colorway retry limit reached.")
        plan = self.session.scalar(select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == tenant_id, RrugcSourcePlanModel.id == row.source_plan_id,
        ))
        if plan is None or plan.status == "missing" or plan.source_revision != row.source_revision:
            raise ColorwayError("colorway_source_changed", "Source design changed; old job cannot be retried.")
        if hashlib.sha256(_stock_bytes(self.settings, row.color_key)).hexdigest() != row.stock_sha256:
            raise ColorwayError("colorway_stock_changed", "Stock image changed; old job cannot be retried.")
        row.retry_count += 1
        row.status = "queued"
        row.last_error_code = row.last_error_message = None
        self._enqueue(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def list(self, *, tenant_id: str, source_ids: list[str]) -> list[dict]:
        ids = list(dict.fromkeys(source_ids))
        if not ids:
            return []
        if len(ids) > 100:
            raise ColorwayError("colorway_list_limit", "Request at most 100 source IDs.", 422)
        rows = self.session.execute(select(RrugcColorwayJobModel, ProcessingJobModel).join(
            RrugcSourcePlanModel,
            (RrugcSourcePlanModel.id == RrugcColorwayJobModel.source_plan_id)
            & (RrugcSourcePlanModel.tenant_id == tenant_id)
            & (RrugcSourcePlanModel.source_revision == RrugcColorwayJobModel.source_revision),
        ).outerjoin(
            ProcessingJobModel, (ProcessingJobModel.id == RrugcColorwayJobModel.processing_job_id)
            & (ProcessingJobModel.tenant_id == tenant_id),
        ).where(RrugcColorwayJobModel.tenant_id == tenant_id,
                RrugcColorwayJobModel.source_plan_id.in_(ids)))
        return [
            {"id": row.id, "source_plan_id": row.source_plan_id, "color_key": row.color_key,
             "color_name": dict(COLORS)[row.color_key], "status": effective_status(row, processing),
             "retry_count": row.retry_count,
             "attempt_count": processing.attempt_count if processing else 0,
             "error_code": row.last_error_code or (processing.last_error_code if processing else None),
             "output_url": f"/api/v1/realistic-review-ugc/colorways/{row.id}/output"
             if row.status == "completed" and row.output_remote_file_id else None}
            for row, processing in rows
        ]


class ColorwayGenerateHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except CodexImageProviderError as exc:
            self._mark_failure(context, exc.code, "Colorway provider unavailable.", terminal=not (exc.retryable or exc.defer_seconds))
            if exc.defer_seconds:
                return DeferredJobOutcome(exc.code, "Colorway provider is busy.",
                                          datetime.now(timezone.utc) + timedelta(seconds=max(60, exc.defer_seconds)))
            return JobHandlerResult.retryable(exc.code, "Colorway provider unavailable.") if exc.retryable else JobHandlerResult.non_retryable(exc.code, "Colorway provider unavailable.")
        except (StorageProviderError, RrugcGenerationHandlerError) as exc:
            code = getattr(exc, "code", "colorway_storage_error")
            retryable = bool(getattr(exc, "retryable", True))
            self._mark_failure(context, code, "Source or storage temporarily unavailable.", terminal=not retryable)
            return JobHandlerResult.retryable(code, "Source or storage temporarily unavailable.") if retryable else JobHandlerResult.non_retryable(code, "Source or storage unavailable.")
        except Stage2SkillRegistryError as exc:
            self._mark_failure(context, exc.code, exc.message, terminal=True)
            return JobHandlerResult.non_retryable(exc.code, exc.message)
        except ColorwayError as exc:
            self._mark_failure(context, exc.code, exc.message, terminal=True)
            return JobHandlerResult.non_retryable(exc.code, exc.message)
        except Exception:
            context.logger.exception("rrugc_colorway_generation_failed", extra={"job_id": context.job.entity_id})
            self._mark_failure(context, "colorway_internal_error", "Colorway generation failed.")
            return JobHandlerResult.retryable("colorway_internal_error", "Colorway generation failed.")

    def _mark_failure(self, context: JobHandlerContext, code: str, message: str, terminal: bool = False) -> None:
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcColorwayJobModel).where(
                RrugcColorwayJobModel.tenant_id == context.job.tenant_id,
                RrugcColorwayJobModel.id == context.job.entity_id))
            if row and row.status != "completed":
                row.status = "failed" if terminal else "queued"
                row.last_error_code = code[:100]
                row.last_error_message = message[:500]
                session.commit()

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        if (context.job.job_type != JOB_TYPE or
            context.job.payload.get("colorway_job_id") != context.job.entity_id):
            return JobHandlerResult.non_retryable("colorway_payload_invalid", "Invalid colorway payload.")
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        settings = self.settings or get_settings()
        if not (settings.PROCESSING_JOBS_ENABLED and settings.IMAGE_GENERATION_ENABLED
                and settings.MANAGED_ASSET_STORAGE_ENABLED
                and getattr(settings, "CODEX_IMAGE_GENERATION_ENABLED", False)):
            return JobHandlerResult.non_retryable("colorway_disabled", "Colorway generator disabled.")
        storage = context.dependencies.storage_provider
        if storage is None:
            raise RrugcGenerationHandlerError("managed_storage_unavailable", "Managed storage unavailable.", retryable=True)

        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcColorwayJobModel).where(
                RrugcColorwayJobModel.tenant_id == context.job.tenant_id,
                RrugcColorwayJobModel.id == context.job.entity_id).with_for_update())
            if row is None:
                return JobHandlerResult.non_retryable("colorway_not_found", "Colorway job missing.")
            if row.status == "completed":
                return JobHandlerResult.completed()
            plan = session.scalar(select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.tenant_id == context.job.tenant_id,
                RrugcSourcePlanModel.id == row.source_plan_id))
            if plan is None or plan.status == "missing" or plan.source_revision != row.source_revision:
                raise ColorwayError("colorway_source_changed", "Source design changed since queueing.")
            verify_stage2_skill_runtime(settings=settings, skill_source=row.skill_source,
                                         skill_id=row.skill_id, skill_name=row.skill_name,
                                         skill_version=row.skill_version)
            if row.skill_bundle_sha256 != installed_stage2_skill_sha256(row.skill_name, settings=settings):
                raise ColorwayError("colorway_skill_changed", "Selected Skill changed since queueing.")
            stock_data = _stock_bytes(settings, row.color_key)
            if hashlib.sha256(stock_data).hexdigest() != row.stock_sha256:
                raise ColorwayError("colorway_stock_changed", "The pinned stock photo changed since queueing.")
            source_file_id, source_mime, source_size = (
                plan.source_file_id, plan.source_mime_type, plan.source_size_bytes)
            folder_id, source_id, color_key, color_name, skill_name = (
                plan.source_parent_folder_id, plan.id, row.color_key, dict(COLORS)[row.color_key], row.skill_name)
            row.status = "running"
            row.started_at = row.started_at or datetime.now(timezone.utc)
            session.commit()

        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=context.job.tenant_id,
            asset_id="rrugc-colorway-source:" + source_id,
            remote_file_id=source_file_id, content_type=source_mime, size_bytes=source_size,
        ))
        source_bytes = bytearray()
        try:
            async for chunk in stream.body:
                source_bytes.extend(chunk)
                if len(source_bytes) > MAX_REFERENCE_BYTES:
                    raise ColorwayError("colorway_source_too_large", "Embroidery input is too large.")
        finally:
            await stream.close()
        stock = _prepared_image(stock_data)
        design = _prepared_image(bytes(source_bytes))
        prompt = (
            "Apply the embroidery from the design reference to the FRONT CENTER of the "
            f"Valucap 8869 five-panel twill cap shown in the base stock photo. Official cap color: {color_name}. "
            "Use the stock photo for the exact cap shape, crown color, bill color and perspective. "
            "Preserve the original embroidery design, letter spelling, stitch details, colors and placement. "
            "Do not invent lettering, change logos, change cap color, or add side embroidery. "
            "Generate one realistic, sharp ecommerce product photo. The stock color is immutable."
        )
        runner = CodexImageGenRunner(CodexImageRunnerConfig(
            binary=str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex"),
            codex_home=str(getattr(settings, "CODEX_IMAGE_HOME", "/var/lib/creative-asset-manager/codex")),
            staging_root=str(getattr(settings, "IMAGE_GENERATION_STAGING_ROOT", "/var/lib/creative-asset-manager/image-generation")),
            skill_name=skill_name, timeout_seconds=int(getattr(settings, "CODEX_IMAGE_TIMEOUT_SECONDS", 900)),
            model=str(getattr(settings, "CODEX_IMAGE_MODEL", "")).strip() or None,
        ))
        try:
            generated = await runner.generate_from_references(
                attempt_id=context.job.entity_id, person=stock,
                references=[ReferenceImageInput(image=design, role="design_reference", label="embroidery")],
                prompt=prompt,
            )
        finally:
            runner.cleanup_attempt(context.job.entity_id)
        try:
            if len(generated.image_bytes) > MAX_REFERENCE_BYTES:
                raise ValueError("Generated image is too large")
            with Image.open(BytesIO(generated.image_bytes)) as image:
                width, height = image.size
                expected_mime = {
                    "PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
                }.get(image.format)
                if (expected_mime != generated.mime_type or width < 512 or height < 512
                        or width * height > MAX_REFERENCE_PIXELS):
                    raise ValueError("Generated image format or dimensions are invalid")
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise RrugcGenerationHandlerError(
                "colorway_output_invalid", "Generated colorway image did not pass basic validation.",
                retryable=True,
            ) from exc
        stored = await storage.store_asset(StoreAssetInput(
            tenant_id=context.job.tenant_id,
            content_hash=hashlib.sha256(generated.image_bytes).hexdigest(),
            body=_bytes_body(generated.image_bytes),
            asset_id="rrugc-colorway:" + context.job.entity_id,
            content_type=generated.mime_type,
            size_bytes=len(generated.image_bytes),
            filename=f"output_{color_key}_{context.job.entity_id}{_output_extension(generated.mime_type)}",
            destination_folder_id=folder_id or None,
        ))
        if not stored.remote_file_id:
            raise StorageProviderError("Generated stock photo was not saved.", retryable=True,
                                       code="colorway_storage_failed")
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcColorwayJobModel).where(
                RrugcColorwayJobModel.tenant_id == context.job.tenant_id,
                RrugcColorwayJobModel.id == context.job.entity_id).with_for_update())
            if row is None:
                return JobHandlerResult.non_retryable("colorway_not_found", "Colorway job missing.")
            row.status = "completed"
            row.output_remote_file_id = stored.remote_file_id
            row.output_content_type = generated.mime_type
            row.output_size_bytes = len(generated.image_bytes)
            row.output_width, row.output_height = width, height
            row.completed_at = datetime.now(timezone.utc)
            row.last_error_code = row.last_error_message = None
            session.commit()
        context.logger.info("rrugc_colorway_completed",
                            extra={"job_id": context.job.entity_id, "color_key": color_key})
        return JobHandlerResult.completed()
