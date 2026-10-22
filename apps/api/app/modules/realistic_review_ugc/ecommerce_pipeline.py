"""Independent, pinned Ecommerce single-image jobs for four GatorHats 8869 views."""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import OpenStoredAssetInput, StoreAssetInput, StorageProviderError
from app.modules.image_generation.providers import ReferenceImageInput
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.codex_account_manager import CodexAccountPoolBusy, acquire_worker_profile
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.ecommerce_input_contract import SHOTS_BY_STAGE
from app.modules.realistic_review_ugc.ecommerce_stock_evidence import read_release_stock
from app.modules.realistic_review_ugc.generation_handler import (
    RrugcGenerationHandlerError, _bytes_body, _output_extension, _prepared_image,
    MAX_REFERENCE_BYTES, MAX_REFERENCE_PIXELS,
)
from app.modules.realistic_review_ugc.model import (
    RrugcEcommerceJobModel, RrugcSourcePlanModel, RrugcStageSkillDefaultModel,
    RrugcStage2SkillRegistryModel,
)
from app.modules.realistic_review_ugc.output_versions import save_output_version
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillRegistryError, resolve_stage2_skill,
    verify_stage2_skill_runtime, installed_stage2_skill_sha256,
)
from app.modules.realistic_review_ugc.skill_registry import assert_skill_enabled
from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError

JOB_TYPE = "rrugc_ecommerce_generate"
MAX_MANUAL_RETRIES = 3

# EC2 is synthesized from verified front and right-side references. Never
# mislabel side.jpg as a dedicated 3/4 stock photograph.
STOCK_ROLES = {
    "prod_s2_ec1": ("stock_front",),
    "prod_s2_ec2": ("stock_front", "stock_side_90"),
    "prod_s2_ec3": ("stock_side_90",),
    "prod_s2_ec4": ("stock_back",),
}

VIEW_PROMPTS = {
    "prod_s2_ec1": (
        "FRONT high straight-on catalog photo. Apply exactly the original embroidery "
        "inside the cap's approved front embroidery area, matching the Blueprint reference "
        "frame on the original 4:5 hat photograph: left 15.2%, top 30.9%, width 69.6%, "
        "height 23.1%. Preserve the artwork aspect ratio without distortion, fitting "
        "within this full guide rectangle. Preserve exact lettering, thread colors, "
        "stitch structure and the five-panel crown. No design outside the guide area."
    ),
    "prod_s2_ec2": (
        "FRONT RIGHT THREE-QUARTER high catalog photo. Rotate the exact Valucap 8869 "
        "five-panel cap from the official frontal view toward the right. Use the separate "
        "right-side stock photo ONLY as evidence of its side geometry, not as a 3/4 photo. "
        "Preserve the already embroidered front-reference design, scaling and stitch colors "
        "with correct perspective on the FRONT crown, without invented side embroidery."
    ),
    "prod_s2_ec3": (
        "TRUE RIGHT SIDE 90 degree high catalog photo. Match the supplied exact stock "
        "right-side geometry and cap colors. Use the embroidered front reference for "
        "continuity; only show portions of the front embroidery visible naturally "
        "from this angle. Do not add any invented side writing."
    ),
    "prod_s2_ec4": (
        "TRUE BACK high catalog photo. Preserve the precise rear view from the official "
        "stock cap photograph. Do not invent any rear embroidery, logo or additional text. "
        "Show the rear crown and strap faithfully, sharp and well lit."
    ),
}


class EcommerceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _stock_refs(settings: Settings, stage: str) -> tuple[list[tuple[str, bytes]], str]:
    if stage not in STOCK_ROLES:
        raise EcommerceError("ecommerce_stage_invalid", "Unknown Ecommerce photo slot.", 422)
    # The checkout root is also available in the immutable production release.
    project_root = Path(__file__).resolve().parents[5]
    try:
        refs = [(role, read_release_stock(role, project_root=project_root)[0])
                for role in STOCK_ROLES[stage]]
    except (OSError, ValueError) as exc:
        raise EcommerceError("ecommerce_stock_unavailable",
                             "A verified GatorHats stock photo is unavailable.", 503) from exc
    digest = hashlib.sha256(b"".join(
        role.encode("utf-8") + b":" + hashlib.sha256(content).digest()
        for role, content in refs
    )).hexdigest()
    return refs, digest


def _source_plan(session: Session, tenant_id: str, plan_id: str,
                 *, lock: bool = False) -> RrugcSourcePlanModel:
    query = select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.tenant_id == tenant_id,
        RrugcSourcePlanModel.id == plan_id,
        RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
    )
    if lock:
        query = query.with_for_update()
    plan = session.scalar(query)
    if (plan is None or plan.status == "missing"
            or plan.source_mime_type.lower() not in {"image/png", "image/jpeg"}
            or not plan.source_file_id):
        raise EcommerceError("ecommerce_source_invalid",
                             "Select an available Product Stage 1 image design.", 422)
    return plan


def _assigned_skill(session: Session, settings: Settings, tenant_id: str, stage: str):
    assignment = session.get(RrugcStageSkillDefaultModel, (tenant_id, stage))
    selected = session.get(RrugcStage2SkillRegistryModel, assignment.registry_id) if assignment and assignment.registry_id else None
    if (not selected or selected.tenant_id != tenant_id or not selected.enabled
            or selected.deleted_at is not None or selected.validation_status != "valid"):
        raise EcommerceError("ecommerce_skill_missing",
                             "Assign an enabled single-image Skill to this Ecommerce slot first.", 409)
    try:
        resolved = resolve_stage2_skill(
            settings=settings, skill_source=selected.source, skill_id=selected.skill_id,
            skill_name=selected.skill_name,
            skill_version=selected.synced_version or selected.default_version,
            fallback_skill_name=selected.skill_name,
        )
        assert_skill_enabled(session, tenant_id=tenant_id, source=resolved.source,
                             skill_id=resolved.skill_id, skill_name=resolved.skill_name)
        if resolved.skill_name == "gatorhats-8869-ecommerce-v2-4":
            raise EcommerceError("ecommerce_bulk_skill_invalid",
                                 "Choose one image-specific Skill per slot.", 422)
        return resolved
    except Stage2SkillRegistryError as exc:
        raise EcommerceError(exc.code, exc.message, exc.status_code) from exc


def _front_job(session: Session, plan: RrugcSourcePlanModel) -> RrugcEcommerceJobModel:
    front = session.scalar(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.tenant_id == plan.tenant_id,
        RrugcEcommerceJobModel.source_plan_id == plan.id,
        RrugcEcommerceJobModel.source_revision == plan.source_revision,
        RrugcEcommerceJobModel.stage == "prod_s2_ec1",
        RrugcEcommerceJobModel.status == "completed",
        RrugcEcommerceJobModel.output_remote_file_id.is_not(None),
    ))
    if not front:
        raise EcommerceError("ecommerce_front_prerequisite",
                             "Run and complete Ecommerce Skill 1 before Skills 2 and 3.")
    return front


def _effective_status(row: RrugcEcommerceJobModel, processing: ProcessingJobModel | None):
    if row.status == "completed":
        return "completed"
    if processing and processing.status == "failed":
        return "failed"
    if processing and processing.status == "processing":
        return "running"
    return row.status


class EcommerceService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def _enqueue(self, row: RrugcEcommerceJobModel) -> None:
        count = int(self.session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == row.tenant_id,
            ProcessingJobModel.entity_type == "rrugc_ecommerce_job",
            ProcessingJobModel.entity_id == row.id,
        )) or 0)
        processing = ProcessingRepository(self.session, self.settings).create_job(
            tenant_id=row.tenant_id, job_type=JOB_TYPE, entity_type="rrugc_ecommerce_job",
            entity_id=row.id, idempotency_key=f"rrugc-ecommerce:{row.id}:{count}",
            payload={"ecommerce_job_id": row.id}, priority=20, max_attempts=3,
            provider_key="codex", provider_scope="ai",
        )
        row.processing_job_id = processing.id

    def queue(self, *, tenant_id: str, user_id: str, source_plan_id: str,
              stage: str) -> dict:
        if stage not in SHOTS_BY_STAGE:
            raise EcommerceError("ecommerce_stage_invalid", "Unknown photo skill slot.", 422)
        if not all((
            self.settings.PROCESSING_JOBS_ENABLED,
            self.settings.IMAGE_GENERATION_ENABLED,
            self.settings.MANAGED_ASSET_STORAGE_ENABLED,
            self.settings.CODEX_IMAGE_GENERATION_ENABLED,
        )):
            raise EcommerceError("ecommerce_execution_disabled", "Image generation is disabled.", 503)
        plan = _source_plan(self.session, tenant_id, source_plan_id, lock=True)
        _refs, stock_digest = _stock_refs(self.settings, stage)
        skill = _assigned_skill(self.session, self.settings, tenant_id, stage)
        front = _front_job(self.session, plan) if stage in ("prod_s2_ec2", "prod_s2_ec3") else None
        row = self.session.scalar(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.tenant_id == tenant_id,
            RrugcEcommerceJobModel.source_plan_id == source_plan_id,
            RrugcEcommerceJobModel.source_revision == plan.source_revision,
            RrugcEcommerceJobModel.stage == stage,
        ).with_for_update())
        if row is not None:
            return {"job_id": row.id, "status": row.status, "already_exists": True}
        row = RrugcEcommerceJobModel(
            id=str(uuid4()), tenant_id=tenant_id, source_plan_id=source_plan_id,
            source_revision=plan.source_revision, stage=stage,
            stock_sha256=stock_digest,
            front_reference_remote_file_id=front.output_remote_file_id if front else None,
            front_reference_content_type=front.output_content_type if front else None,
            front_reference_size_bytes=front.output_size_bytes if front else None,
            skill_source=skill.source,
            skill_id=skill.skill_id, skill_name=skill.skill_name,
            skill_version=skill.skill_version,
            skill_bundle_sha256=installed_stage2_skill_sha256(skill.skill_name, settings=self.settings),
            created_by_user_id=user_id, status="queued",
        )
        self.session.add(row)
        self.session.flush()
        self._enqueue(row)
        self.session.commit()
        return {"job_id": row.id, "status": row.status, "already_exists": False}

    def retry(self, *, tenant_id: str, job_id: str) -> dict:
        row = self.session.scalar(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.id == job_id,
            RrugcEcommerceJobModel.tenant_id == tenant_id,
        ).with_for_update())
        if row is None:
            raise EcommerceError("ecommerce_job_not_found", "Job not found.", 404)
        processing = self.session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
        if _effective_status(row, processing) != "failed" or row.retry_count >= MAX_MANUAL_RETRIES:
            raise EcommerceError("ecommerce_retry_unavailable",
                                 "Only failed jobs with remaining retries can be retried.")
        plan = _source_plan(self.session, tenant_id, row.source_plan_id, lock=True)
        if plan.source_revision != row.source_revision:
            raise EcommerceError("ecommerce_source_changed", "Design revision changed.")
        _, current_digest = _stock_refs(self.settings, row.stage)
        if current_digest != row.stock_sha256:
            raise EcommerceError("ecommerce_stock_changed", "Official stock revision changed.")
        if row.stage in ("prod_s2_ec2", "prod_s2_ec3") and not row.front_reference_remote_file_id:
            raise EcommerceError("ecommerce_front_reference_missing", "Pinned Front reference is missing.")
        row.retry_count += 1
        row.status = "queued"
        row.last_error_code = row.last_error_message = None
        self._enqueue(row)
        self.session.commit()
        return {"job_id": row.id, "status": row.status, "retry_count": row.retry_count}

    def regenerate(self, *, tenant_id: str, job_id: str) -> dict:
        """Re-run an existing completed image while retaining every previous version."""
        row = self.session.scalar(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.id == job_id,
            RrugcEcommerceJobModel.tenant_id == tenant_id,
        ).with_for_update())
        if not row:
            raise EcommerceError("ecommerce_job_not_found", "Job not found.", 404)
        if row.status != "completed" or not row.output_remote_file_id:
            raise EcommerceError("ecommerce_regenerate_unavailable",
                                 "Only a completed Ecommerce image can be regenerated.")
        plan = _source_plan(self.session, tenant_id, row.source_plan_id, lock=True)
        if plan.source_revision != row.source_revision:
            raise EcommerceError("ecommerce_source_changed", "Design revision changed.")
        _, digest = _stock_refs(self.settings, row.stage)
        if digest != row.stock_sha256:
            raise EcommerceError("ecommerce_stock_changed", "Official stock reference changed.")
        if row.stage in ("prod_s2_ec2", "prod_s2_ec3"):
            front = _front_job(self.session, plan)
            row.front_reference_remote_file_id = front.output_remote_file_id
            row.front_reference_content_type = front.output_content_type
            row.front_reference_size_bytes = front.output_size_bytes
        # Keep the version's original Skill pinned; do not silently switch to
        # another assigned Skill in a historical photo job.
        row.status = "queued"
        row.retry_count = 0
        row.started_at = None
        row.completed_at = None
        row.last_error_code = row.last_error_message = None
        self._enqueue(row)
        self.session.commit()
        return {"job_id": row.id, "status": row.status}

    def list(self, *, tenant_id: str, source_ids: list[str]) -> list[dict]:
        ids = list(dict.fromkeys(source_ids))
        if not ids:
            return []
        if len(ids) > 100:
            raise EcommerceError("ecommerce_list_limit", "At most 100 source IDs.", 422)
        rows = self.session.execute(select(RrugcEcommerceJobModel, ProcessingJobModel).join(
            RrugcSourcePlanModel,
            (RrugcSourcePlanModel.id == RrugcEcommerceJobModel.source_plan_id)
            & (RrugcSourcePlanModel.tenant_id == tenant_id)
            & (RrugcSourcePlanModel.source_revision == RrugcEcommerceJobModel.source_revision),
        ).outerjoin(ProcessingJobModel,
                    (ProcessingJobModel.id == RrugcEcommerceJobModel.processing_job_id)
                    & (ProcessingJobModel.tenant_id == tenant_id)).where(
            RrugcEcommerceJobModel.tenant_id == tenant_id,
            RrugcEcommerceJobModel.source_plan_id.in_(ids),
        ))
        return [{
            "id": row.id, "source_plan_id": row.source_plan_id, "stage": row.stage,
            "status": _effective_status(row, processing), "retry_count": row.retry_count,
            "attempt_count": processing.attempt_count if processing else 0,
            "error_code": row.last_error_code or (processing.last_error_code if processing else None),
            "output_url": f"/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/{row.id}/output"
                          if row.output_remote_file_id else None,
            "skill_name": row.skill_name,
        } for row, processing in rows]


class EcommerceGenerateHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext):
        try:
            return asyncio.run(self._execute(context))
        except CodexAccountPoolBusy:
            self._failure(context, "codex_account_pool_busy", "All Codex profiles busy.")
            return DeferredJobOutcome("codex_account_pool_busy", "All Codex profiles busy.",
                                      datetime.now(timezone.utc) + timedelta(seconds=60))
        except CodexImageProviderError as exc:
            self._failure(context, exc.code, "Image provider unavailable.",
                          terminal=not (exc.retryable or exc.defer_seconds))
            if exc.defer_seconds:
                return DeferredJobOutcome(exc.code, "Image provider busy.",
                                          datetime.now(timezone.utc) + timedelta(seconds=max(60, exc.defer_seconds)))
            return (JobHandlerResult.retryable(exc.code, "Image provider unavailable.")
                    if exc.retryable else JobHandlerResult.non_retryable(exc.code, "Image provider unavailable."))
        except (EcommerceError, Stage2SkillRegistryError) as exc:
            self._failure(context, exc.code, exc.message, terminal=True)
            return JobHandlerResult.non_retryable(exc.code, exc.message)
        except (StorageProviderError, RrugcGenerationHandlerError) as exc:
            code = getattr(exc, "code", "ecommerce_storage_error")
            retryable = bool(getattr(exc, "retryable", True))
            self._failure(context, code, "Storage or image input unavailable.", terminal=not retryable)
            return (JobHandlerResult.retryable(code, "Storage unavailable.")
                    if retryable else JobHandlerResult.non_retryable(code, "Storage unavailable."))
        except Exception:
            context.logger.exception("ecommerce_generation_failed", extra={"job_id": context.job.entity_id})
            self._failure(context, "ecommerce_internal_error", "Ecommerce generation failed.")
            return JobHandlerResult.retryable("ecommerce_internal_error", "Ecommerce generation failed.")

    def _failure(self, context: JobHandlerContext, code: str, message: str, terminal=False):
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcEcommerceJobModel).where(
                RrugcEcommerceJobModel.tenant_id == context.job.tenant_id,
                RrugcEcommerceJobModel.id == context.job.entity_id,
            ))
            if row and row.status != "completed":
                row.status = "failed" if terminal else "queued"
                row.last_error_code = code[:100]
                row.last_error_message = message[:500]
                session.commit()

    async def _open_bytes(self, storage, context, asset_id, remote_id, mime, size):
        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=context.job.tenant_id, asset_id=asset_id,
            remote_file_id=remote_id, content_type=mime, size_bytes=size,
        ))
        chunks = bytearray()
        try:
            async for chunk in stream.body:
                chunks.extend(chunk)
                if len(chunks) > MAX_REFERENCE_BYTES:
                    raise EcommerceError("ecommerce_input_too_large", "Input image too large.", 422)
        finally:
            await stream.close()
        return bytes(chunks)

    async def _execute(self, context: JobHandlerContext):
        if (context.job.job_type != JOB_TYPE or
                context.job.payload.get("ecommerce_job_id") != context.job.entity_id):
            return JobHandlerResult.non_retryable("ecommerce_payload_invalid", "Invalid job payload.")
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        settings = self.settings or get_settings()
        if not all((settings.PROCESSING_JOBS_ENABLED, settings.IMAGE_GENERATION_ENABLED,
                    settings.MANAGED_ASSET_STORAGE_ENABLED, settings.CODEX_IMAGE_GENERATION_ENABLED)):
            return JobHandlerResult.non_retryable("ecommerce_disabled", "Ecommerce image generation disabled.")
        storage = context.dependencies.storage_provider
        if storage is None:
            raise RrugcGenerationHandlerError("managed_storage_unavailable",
                                              "Managed storage unavailable.", retryable=True)
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcEcommerceJobModel).where(
                RrugcEcommerceJobModel.tenant_id == context.job.tenant_id,
                RrugcEcommerceJobModel.id == context.job.entity_id,
            ).with_for_update())
            if row is None:
                return JobHandlerResult.non_retryable("ecommerce_job_not_found", "Job missing.")
            if row.status == "completed":
                return JobHandlerResult.completed()
            plan = _source_plan(session, context.job.tenant_id, row.source_plan_id)
            if plan.source_revision != row.source_revision:
                raise EcommerceError("ecommerce_source_changed", "Design revision changed.")
            verify_stage2_skill_runtime(settings=settings, skill_source=row.skill_source,
                                        skill_id=row.skill_id, skill_name=row.skill_name,
                                        skill_version=row.skill_version)
            if row.skill_bundle_sha256 != installed_stage2_skill_sha256(row.skill_name, settings=settings):
                raise EcommerceError("ecommerce_skill_changed", "Selected Skill revision changed.")
            refs, digest = _stock_refs(settings, row.stage)
            if digest != row.stock_sha256:
                raise EcommerceError("ecommerce_stock_changed", "Pinned stock content changed.")
            front_asset = None
            if row.stage in ("prod_s2_ec2", "prod_s2_ec3"):
                if not row.front_reference_remote_file_id:
                    raise EcommerceError("ecommerce_front_reference_missing", "Pinned Front input is missing.")
                front_asset = (row.front_reference_remote_file_id,
                               row.front_reference_content_type, row.front_reference_size_bytes)
            plan_id, mime, size, source_file_id = (
                plan.id, plan.source_mime_type, plan.source_size_bytes, plan.source_file_id)
            folder_id, stage, skill_name = plan.source_parent_folder_id, row.stage, row.skill_name
            row.status = "running"
            row.started_at = row.started_at or datetime.now(timezone.utc)
            session.commit()

        # The rear view needs no design and must not accidentally decorate the back.
        references = []
        if stage != "prod_s2_ec4":
            design_bytes = await self._open_bytes(storage, context,
                    "rrugc-ecommerce-design:" + plan_id, source_file_id, mime, size)
            references.append(ReferenceImageInput(
                image=_prepared_image(design_bytes), role="design_reference",
                label="exact embroidery artwork"))
        front_image = None
        if front_asset:
            front_remote, front_mime, front_size = front_asset
            front_image = _prepared_image(await self._open_bytes(
                storage, context, "rrugc-ecommerce-front:" + context.job.entity_id,
                front_remote, front_mime, front_size))
        # Immutable official stock is addressed by its hash, not a transient browser preview.
        base = _prepared_image(refs[0][1])
        if front_image:
            references.append(ReferenceImageInput(
                image=front_image, role="front_reference", label="completed embroidered front photo"))
        if len(refs) > 1:
            references.append(ReferenceImageInput(
                image=_prepared_image(refs[1][1]), role="side_reference",
                label="official true right-side stock geometry"))
        prompt = (
            "Generate ONE photorealistic commercial ecommerce product photograph "
            "of the official Valucap 8869 natural/black five-panel twill cap. "
            + VIEW_PROMPTS[stage] +
            " Keep all cap panel seams, fabric, bill curvature, dark-black/natural fabric "
            "colors and hardware strictly consistent with the official reference photos. "
            "No extra logos, added decorations, invented text, or merged panels."
        )
        binary = str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex")
        primary_home = str(getattr(settings, "CODEX_IMAGE_HOME",
                                   "/var/lib/creative-asset-manager/codex"))
        with context.dependencies.session_factory() as pool_session:
            with acquire_worker_profile(pool_session, context.job.tenant_id, "stage2",
                                         primary_home, binary, skill_name) as (account_id, selected_home):
                pool_session.commit()
                runner = CodexImageGenRunner(CodexImageRunnerConfig(
                    binary=binary, codex_home=selected_home,
                    staging_root=str(getattr(settings, "IMAGE_GENERATION_STAGING_ROOT",
                                            "/var/lib/creative-asset-manager/image-generation")),
                    skill_name=skill_name,
                    timeout_seconds=int(getattr(settings, "CODEX_IMAGE_TIMEOUT_SECONDS", 900)),
                    model=str(getattr(settings, "CODEX_IMAGE_MODEL", "") or "").strip() or None,
                ))
                try:
                    generated = await runner.generate_from_references(
                        attempt_id=context.job.entity_id, person=base, references=references,
                        prompt=prompt,
                    )
                finally:
                    runner.cleanup_attempt(context.job.entity_id)
        try:
            if len(generated.image_bytes) > MAX_REFERENCE_BYTES:
                raise ValueError("Output too large")
            with Image.open(BytesIO(generated.image_bytes)) as image:
                width, height = image.size
                expected = {"PNG":"image/png", "JPEG":"image/jpeg", "WEBP":"image/webp"}.get(image.format)
                if expected != generated.mime_type or width < 512 or height < 512 or width * height > MAX_REFERENCE_PIXELS:
                    raise ValueError("Output dimensions or media type invalid")
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise RrugcGenerationHandlerError("ecommerce_output_invalid",
                            "Generated photo did not pass basic validation.", retryable=True) from exc
        stored = await storage.store_asset(StoreAssetInput(
            tenant_id=context.job.tenant_id,
            content_hash=hashlib.sha256(generated.image_bytes).hexdigest(),
            body=_bytes_body(generated.image_bytes),
            asset_id="rrugc-ecommerce:" + context.job.entity_id + ":" + context.job.id,
            content_type=generated.mime_type, size_bytes=len(generated.image_bytes),
            filename=f"output_ecommerce_{stage}_{context.job.entity_id}_{context.job.id}{_output_extension(generated.mime_type)}",
            destination_folder_id=folder_id or None,
        ))
        if not stored.remote_file_id:
            raise StorageProviderError("Ecommerce output was not saved.", retryable=True,
                                       code="ecommerce_storage_failed")
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcEcommerceJobModel).where(
                RrugcEcommerceJobModel.tenant_id == context.job.tenant_id,
                RrugcEcommerceJobModel.id == context.job.entity_id,
            ).with_for_update())
            if row is None:
                return JobHandlerResult.non_retryable("ecommerce_job_not_found", "Job missing.")
            save_output_version(session, tenant_id=context.job.tenant_id, stage="ecommerce",
                                job=row, remote_file_id=stored.remote_file_id,
                                content_type=generated.mime_type,
                                size_bytes=len(generated.image_bytes), width=width, height=height,
                                processing_job_id=context.job.id,
                                font_info={"input_reference_snapshot": {
                                    "source_revision": row.source_revision,
                                    "stock_sha256": row.stock_sha256,
                                    "skill_bundle_sha256": row.skill_bundle_sha256,
                                    "front_reference_remote_file_id": row.front_reference_remote_file_id,
                                }})
            row.output_remote_file_id = stored.remote_file_id
            row.output_content_type = generated.mime_type
            row.output_size_bytes = len(generated.image_bytes)
            row.output_width, row.output_height = width, height
            row.completed_at = datetime.now(timezone.utc)
            row.status = "completed"
            row.last_error_code = row.last_error_message = None
            session.commit()
        return JobHandlerResult.completed()
