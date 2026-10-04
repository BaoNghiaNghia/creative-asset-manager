from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import OpenStoredAssetInput, StorageProviderError, StoreAssetInput
from app.modules.image_generation.providers import ReferenceImageInput
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.generation_handler import (
    MAX_REFERENCE_BYTES,
    RrugcGenerationHandlerError,
    _bytes_body,
    _output_extension,
    _prepared_image,
)
from app.modules.realistic_review_ugc.model import (
    RrugcCandidateModel,
    RrugcSourcePlanModel,
    RrugcStage2JobModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillRegistryError,
    resolve_stage2_skill,
)
from app.providers.ai.codex_image import (
    CodexImageGenRunner,
    CodexImageProviderError,
    CodexImageRunnerConfig,
    load_codex_skill_manifest,
)


STAGE2_JOB_TYPE = "rrugc_stage2_generate"
DEFAULT_STAGE2_SKILL = "gatorhats-8869-image-studio"
MAX_STAGE2_REFERENCES = 10


class RrugcStage2Error(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class RrugcStage2Service:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.repository = RrugcRepository(session)

    def create_job(
        self,
        *,
        tenant_id: str,
        user_id: str,
        source_plan_id: str,
        selected_candidate_ids: list[str],
        skill_source: str | None = None,
        skill_id: str | None = None,
        skill_name: str | None = None,
        skill_version: str | None = None,
        prompt: str | None = None,
    ) -> tuple[RrugcStage2JobModel, bool]:
        plan = self.session.get(RrugcSourcePlanModel, source_plan_id)
        if plan is None or plan.tenant_id != tenant_id:
            raise RrugcStage2Error(
                "stage2_source_plan_not_found",
                "Embroidery source plan was not found.",
                status_code=404,
            )
        if plan.status != "ready" or not plan.campaign_id:
            raise RrugcStage2Error(
                "stage2_source_plan_not_ready",
                "Stage 1 must finish the embroidery context plan before Stage 2 can run.",
            )
        ids = list(dict.fromkeys(str(value).strip() for value in selected_candidate_ids if str(value).strip()))
        if not ids:
            raise RrugcStage2Error(
                "stage2_references_required",
                "Pick at least one Pinterest reference.",
                status_code=422,
            )
        if len(ids) > MAX_STAGE2_REFERENCES:
            raise RrugcStage2Error(
                "stage2_reference_limit_exceeded",
                "Pick at most 10 Pinterest references.",
                status_code=422,
            )

        try:
            resolved = resolve_stage2_skill(
                settings=self.settings,
                skill_source=skill_source,
                skill_id=skill_id,
                skill_name=skill_name,
                skill_version=skill_version,
                fallback_skill_name=DEFAULT_STAGE2_SKILL,
            )
        except Stage2SkillRegistryError as exc:
            raise RrugcStage2Error(
                exc.code,
                exc.message,
                status_code=exc.status_code,
            ) from exc
        resolved_skill = resolved.skill_name
        codex_home = str(
            getattr(
                self.settings,
                "CODEX_IMAGE_HOME",
                "/var/lib/creative-asset-manager/codex",
            )
        )
        manifest = load_codex_skill_manifest(codex_home, resolved_skill)
        if manifest is None or "image_studio" not in manifest.workflows:
            raise RrugcStage2Error(
                "stage2_skill_invalid",
                "Stage 2 requires an installed image_studio Codex skill.",
                status_code=422,
            )

        rows = list(
            self.session.query(RrugcCandidateModel).filter(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == plan.campaign_id,
                RrugcCandidateModel.id.in_(ids),
            )
        )
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(ids):
            raise RrugcStage2Error(
                "stage2_reference_not_found",
                "One or more selected Pinterest references no longer belong to this embroidery job.",
                status_code=422,
            )

        snapshot: list[dict] = []
        for position, candidate_id in enumerate(ids):
            row = by_id[candidate_id]
            signal = dict(row.ai_signal_json or {})
            if signal.get("reference_manual_label") in {"bad", "ai"}:
                raise RrugcStage2Error(
                    "stage2_reference_rejected",
                    "Rejected or AI-marked Pinterest references cannot be used for generation.",
                    status_code=422,
                )
            if not row.remote_file_id:
                raise RrugcStage2Error(
                    "stage2_reference_not_durable",
                    "Selected Pinterest references must finish Drive import before generation.",
                )
            snapshot.append(
                {
                    "candidate_id": row.id,
                    "position": position,
                    "pin_url": row.pin_url,
                    "image_url": row.image_url,
                    "remote_file_id": row.remote_file_id,
                    "remote_folder_id": row.remote_folder_id,
                    "content_hash": row.content_hash,
                    "content_type": (
                        {
                            "jpg": "image/jpeg",
                            "jpeg": "image/jpeg",
                            "png": "image/png",
                            "webp": "image/webp",
                        }.get(str(row.image_format).lower())
                        if row.image_format
                        else None
                    ),
                    "width": row.width,
                    "height": row.height,
                }
            )

        raw_key = "|".join(
            [
                plan.id,
                plan.source_revision,
                resolved.source,
                resolved.skill_id or "",
                resolved_skill,
                resolved.skill_version or "",
                *ids,
                (prompt or "").strip(),
            ]
        )
        idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        existing = self.repository.stage2_job_by_key(tenant_id, idempotency_key)
        if existing is not None:
            return existing, False

        now = datetime.now(timezone.utc)
        row = RrugcStage2JobModel(
            tenant_id=tenant_id,
            source_plan_id=plan.id,
            campaign_id=plan.campaign_id,
            source_revision=plan.source_revision,
            skill_name=resolved_skill,
            skill_source=resolved.source,
            skill_id=resolved.skill_id,
            skill_version=resolved.skill_version,
            selected_candidate_ids_json=ids,
            selected_reference_snapshot_json=snapshot,
            prompt_text=(prompt or "").strip() or None,
            status="queued",
            idempotency_key=idempotency_key,
            queued_at=now,
            created_by_user_id=user_id,
        )
        try:
            self.session.add(row)
            self.session.flush()
            processing_job = ProcessingRepository(self.session, self.settings).create_job(
                tenant_id=tenant_id,
                job_type=STAGE2_JOB_TYPE,
                entity_type="rrugc_stage2_job",
                entity_id=row.id,
                idempotency_key=f"rrugc-stage2:{row.id}",
                payload={"stage2_job_id": row.id},
                priority=25,
                max_attempts=3,
                provider_key="codex",
                provider_scope="ai",
            )
            row.processing_job_id = processing_job.id
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.stage2_job_by_key(tenant_id, idempotency_key)
            if existing is not None:
                return existing, False
            raise
        self.session.refresh(row)
        return row, True


class RrugcStage2GenerateJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(
        self,
        context: JobHandlerContext,
    ) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except CodexImageProviderError as exc:
            retryable = exc.defer_seconds is not None or exc.retryable
            self._mark_failure(
                context,
                exc.code,
                str(exc),
                terminal=not retryable,
            )
            if exc.defer_seconds is not None:
                return DeferredJobOutcome(
                    exc.code,
                    str(exc),
                    datetime.now(timezone.utc)
                    + timedelta(seconds=max(60, int(exc.defer_seconds))),
                )
            return (
                JobHandlerResult.retryable(exc.code, str(exc))
                if exc.retryable
                else JobHandlerResult.non_retryable(exc.code, str(exc))
            )
        except (RrugcGenerationHandlerError, StorageProviderError) as exc:
            code = getattr(exc, "code", "stage2_reference_unavailable")
            retryable = bool(getattr(exc, "retryable", True))
            self._mark_failure(context, code, str(exc), terminal=not retryable)
            return (
                JobHandlerResult.retryable(code, str(exc))
                if retryable
                else JobHandlerResult.non_retryable(code, str(exc))
            )
        except Exception as exc:
            self._mark_failure(context, "stage2_generation_internal_error", str(exc))
            context.logger.exception(
                "rrugc_stage2_generation_failed",
                extra={
                    "stage2_job_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                },
            )
            return JobHandlerResult.retryable(
                "stage2_generation_internal_error",
                "Stage 2 image generation failed.",
            )

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        job_id = str(context.job.payload.get("stage2_job_id") or "")
        if (
            context.job.job_type != STAGE2_JOB_TYPE
            or not job_id
            or job_id != context.job.entity_id
        ):
            return JobHandlerResult.non_retryable(
                "stage2_job_invalid",
                "Stage 2 job payload is invalid.",
            )
        if context.is_cancelled:
            return JobHandlerResult.cancelled()

        settings = self.settings or get_settings()
        if not (
            settings.PROCESSING_JOBS_ENABLED
            and settings.IMAGE_GENERATION_ENABLED
            and bool(getattr(settings, "CODEX_IMAGE_GENERATION_ENABLED", False))
        ):
            self._mark_failure(
                context,
                "stage2_generation_disabled",
                "Codex image generation is disabled.",
                terminal=True,
            )
            return JobHandlerResult.non_retryable(
                "stage2_generation_disabled",
                "Codex image generation is disabled.",
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
            row = repository.lock_stage2_job(context.job.tenant_id, job_id)
            if row is None:
                return JobHandlerResult.non_retryable(
                    "stage2_job_not_found",
                    "Stage 2 job was not found.",
                )
            if row.status == "completed":
                return JobHandlerResult.completed()
            plan = session.get(RrugcSourcePlanModel, row.source_plan_id)
            if plan is None or plan.source_revision != row.source_revision:
                row.status = "failed"
                row.last_error_code = "stage2_source_changed"
                row.last_error_message = "The embroidery source changed after this job was queued."
                session.commit()
                return JobHandlerResult.non_retryable(
                    "stage2_source_changed",
                    row.last_error_message,
                )
            row.status = "running"
            row.started_at = row.started_at or datetime.now(timezone.utc)
            row.last_error_code = None
            row.last_error_message = None
            source_file_id = plan.source_file_id
            source_mime_type = plan.source_mime_type
            source_size_bytes = plan.source_size_bytes
            references = list(row.selected_reference_snapshot_json or [])
            skill_name = row.skill_name
            prompt = row.prompt_text or ""
            session.commit()

        source = await self._open_prepared(
            storage,
            tenant_id=context.job.tenant_id,
            asset_id=f"rrugc-stage2-source:{job_id}",
            remote_file_id=source_file_id,
            content_type=source_mime_type,
            size_bytes=source_size_bytes,
        )
        reference_inputs: list[ReferenceImageInput] = []
        for index, item in enumerate(references[:MAX_STAGE2_REFERENCES], start=1):
            remote_file_id = str(item.get("remote_file_id") or "")
            if not remote_file_id:
                continue
            reference_inputs.append(
                ReferenceImageInput(
                    image=await self._open_prepared(
                        storage,
                        tenant_id=context.job.tenant_id,
                        asset_id=f"rrugc-stage2-ref:{item.get('candidate_id') or index}",
                        remote_file_id=remote_file_id,
                        content_type=(
                            str(item.get("content_type"))
                            if item.get("content_type")
                            else None
                        ),
                    ),
                    role="target_image",
                    label=f"pinterest-context-{index}",
                )
            )
        if not reference_inputs:
            raise RrugcGenerationHandlerError(
                "stage2_references_unavailable",
                "No selected Pinterest reference could be loaded.",
            )

        runner = CodexImageGenRunner(
            CodexImageRunnerConfig(
                binary=str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex"),
                codex_home=str(
                    getattr(
                        settings,
                        "CODEX_IMAGE_HOME",
                        "/var/lib/creative-asset-manager/codex",
                    )
                ),
                staging_root=str(
                    getattr(
                        settings,
                        "IMAGE_GENERATION_STAGING_ROOT",
                        "/var/lib/creative-asset-manager/image-generation",
                    )
                ),
                skill_name=skill_name,
                timeout_seconds=int(getattr(settings, "CODEX_IMAGE_TIMEOUT_SECONDS", 900)),
                model=(
                    str(getattr(settings, "CODEX_IMAGE_MODEL", "")).strip()
                    or None
                ),
            )
        )
        generation_prompt = (
            "Stage 2 master generation. The edit target is the authoritative "
            "embroidered-hat source. Preserve the visible embroidery identity, "
            "wording, layout and hat construction. The Pinterest target_image "
            "references are context/composition/style anchors only; do not copy "
            "their branding or artwork. Create one photorealistic master image "
            "that can anchor later variants."
        )
        if prompt:
            generation_prompt += "\nAdditional operator instruction: " + prompt

        try:
            result = await runner.generate_from_references(
                attempt_id=job_id,
                person=source,
                references=reference_inputs,
                prompt=generation_prompt,
            )
        finally:
            runner.cleanup_attempt(job_id)

        try:
            with Image.open(BytesIO(result.image_bytes)) as image:
                image.load()
                width, height = image.size
        except Exception as exc:
            raise RrugcGenerationHandlerError(
                "stage2_output_invalid",
                "Generated Stage 2 output is invalid.",
            ) from exc

        content_hash = hashlib.sha256(result.image_bytes).hexdigest()
        stored = await storage.store_asset(
            StoreAssetInput(
                tenant_id=context.job.tenant_id,
                content_hash=content_hash,
                body=_bytes_body(result.image_bytes),
                asset_id=f"rrugc-stage2:{job_id}",
                content_type=result.mime_type,
                size_bytes=len(result.image_bytes),
                filename=f"rrugc-stage2-{job_id}{_output_extension(result.mime_type)}",
            )
        )
        if not stored.remote_file_id:
            raise RrugcGenerationHandlerError(
                "stage2_storage_invalid",
                "Managed Drive returned no file identifier for the Stage 2 output.",
                retryable=True,
            )

        with context.dependencies.session_factory() as session:
            row = RrugcRepository(session).lock_stage2_job(
                context.job.tenant_id,
                job_id,
            )
            if row is None:
                return JobHandlerResult.non_retryable(
                    "stage2_job_not_found",
                    "Stage 2 job disappeared after generation.",
                )
            row.provider_request_id = result.provider_request_id
            row.output_content_hash = content_hash
            row.output_content_type = result.mime_type
            row.output_size_bytes = len(result.image_bytes)
            row.output_width = width
            row.output_height = height
            row.output_remote_file_id = stored.remote_file_id
            row.output_remote_folder_id = stored.remote_folder_id
            row.output_web_url = stored.web_url
            row.status = "completed"
            row.completed_at = datetime.now(timezone.utc)
            row.last_error_code = None
            row.last_error_message = None
            session.commit()

        context.logger.info(
            "rrugc_stage2_generation_completed",
            extra={
                "stage2_job_id": job_id,
                "tenant_id": context.job.tenant_id,
                "skill_name": skill_name,
                "reference_count": len(reference_inputs),
                "output_width": width,
                "output_height": height,
            },
        )
        return JobHandlerResult.completed()

    async def _open_prepared(
        self,
        storage,
        *,
        tenant_id: str,
        asset_id: str,
        remote_file_id: str,
        content_type: str | None = None,
        size_bytes: int | None = None,
    ):
        stream = await storage.open_asset(
            OpenStoredAssetInput(
                tenant_id=tenant_id,
                asset_id=asset_id,
                remote_file_id=remote_file_id,
                content_type=content_type,
                size_bytes=size_bytes,
            )
        )
        data = bytearray()
        try:
            async for chunk in stream.body:
                data.extend(chunk)
                if len(data) > MAX_REFERENCE_BYTES:
                    raise RrugcGenerationHandlerError(
                        "stage2_reference_too_large",
                        "Stage 2 input image exceeds the size limit.",
                    )
        finally:
            await stream.close()
        return _prepared_image(bytes(data))

    @staticmethod
    def _mark_failure(
        context: JobHandlerContext,
        code: str,
        message: str,
        *,
        terminal: bool = False,
    ) -> None:
        job_id = str(context.job.payload.get("stage2_job_id") or "")
        if not job_id:
            return
        with context.dependencies.session_factory() as session:
            row = RrugcRepository(session).lock_stage2_job(
                context.job.tenant_id,
                job_id,
            )
            if row is None or row.status == "completed":
                return
            row.status = "failed" if terminal else "queued"
            row.last_error_code = str(code)[:100]
            row.last_error_message = str(message)[:1000]
            session.commit()
