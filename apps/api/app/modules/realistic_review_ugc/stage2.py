from __future__ import annotations

import asyncio
import hashlib
import random
from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import uuid4

from PIL import Image
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import OpenStoredAssetInput, StorageProviderError, StoreAssetInput
from app.modules.image_generation.providers import ReferenceImageInput
from app.modules.processing.model import ProcessingJobModel
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
from app.modules.realistic_review_ugc.codex_account_manager import CodexAccountPoolBusy, acquire_worker_profile
from app.modules.realistic_review_ugc.output_versions import save_output_version
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.skill_registry import (
    assert_skill_enabled,
    ensure_skill_registry,
)
from app.modules.realistic_review_ugc.stage3 import RrugcStage3Service
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillRegistryError,
    installed_stage2_skill_sha256,
    resolve_stage2_skill,
    verify_stage2_skill_runtime,
)
from app.providers.ai.codex_image import (
    CodexImageGenRunner,
    CodexImageProviderError,
    CodexImageRunnerConfig,
    load_codex_skill_manifest,
)


STAGE2_JOB_TYPE = "rrugc_stage2_generate"
DEFAULT_STAGE2_SKILL = "gatorhats-8869-image-studio"
MAX_STAGE2_REFERENCES = 3
STAGE2_CANCEL_GRACE_SECONDS = 10
DEFAULT_STAGE2_PROMPT = "8869 Five-Panel Twill Cap\nCenter"


class RrugcStage2Error(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _stage2_source_group(
    session: Session,
    *,
    tenant_id: str,
    plan: RrugcSourcePlanModel,
) -> list[RrugcSourcePlanModel]:
    """Resolve job history against exactly the Stage 3/4 UI group.

    Include temporarily missing members so generated outputs and Pinterest
    duplicate protection survive Drive reconciliation.
    """
    from app.modules.realistic_review_ugc.embroidery_groups import group_source_plan_rows

    candidates = list(
        session.scalars(
            select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.tenant_id == tenant_id,
                RrugcSourcePlanModel.root_folder_id == plan.root_folder_id,
            ).order_by(
                RrugcSourcePlanModel.source_relative_path.asc(),
                RrugcSourcePlanModel.id.asc(),
            )
        )
    )
    # Stage 3/4 source-plan list excludes the intermediate embroidery_ PNGs
    # used by the separate Reviews Stage 2 colorway pipeline. Apply the
    # identical namespace filter before resolving job history.
    from app.modules.realistic_review_ugc.source_plans import RRUGC_SOURCE_ROOT_FOLDER_ID
    if plan.root_folder_id == RRUGC_SOURCE_ROOT_FOLDER_ID:
        candidates = [
            member for member in candidates
            if not member.source_name.casefold().startswith("embroidery_")
        ]
    for members in group_source_plan_rows(candidates):
        if any(member.id == plan.id for member in members):
            return [member for member in members if member.source_file_id] or [plan]
    return [plan]

def _require_stage4_storage(settings: Settings) -> None:
    """Never queue doomed jobs while Managed Drive credentials are absent.

    Credential reconnect is an operator action. This preflight returns a clear
    409 instead of letting the worker burn a generation attempt and report the
    opaque storage_provider_error.
    """
    if settings.is_production and isinstance(
        build_managed_storage_provider(settings), UnconfiguredAssetStorageProvider
    ):
        raise RrugcStage2Error(
            "stage4_managed_drive_not_connected",
            "Stage 4 needs a connected Managed Google Drive account. "
            "Reconnect Managed Storage in Settings and verify its folder before generating or retrying.",
            status_code=409,
        )


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
        _require_stage4_storage(self.settings)
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
        source_group = _stage2_source_group(
            self.session,
            tenant_id=tenant_id,
            plan=plan,
        )
        source_group_ids = [member.id for member in source_group]
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
                "Pick at most 3 Pinterest references per generation run.",
                status_code=422,
            )

        completed_candidate_ids = {
            str(candidate_id)
            for completed_job in self.session.query(RrugcStage2JobModel).filter(
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.source_plan_id.in_(source_group_ids),
                RrugcStage2JobModel.status == "completed",
            )
            for candidate_id in (completed_job.selected_candidate_ids_json or [])
            if str(candidate_id).strip()
        }
        already_generated = [candidate_id for candidate_id in ids if candidate_id in completed_candidate_ids]
        if already_generated:
            raise RrugcStage2Error(
                "stage2_reference_already_generated",
                "One or more selected Pinterest references already have a completed Stage 2 output.",
                status_code=409,
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
        ensure_skill_registry(
            self.session,
            tenant_id=tenant_id,
        )
        try:
            assert_skill_enabled(
                self.session,
                tenant_id=tenant_id,
                source=resolved.source,
                skill_id=resolved.skill_id,
                skill_name=resolved_skill,
            )
        except Stage2SkillRegistryError as exc:
            raise RrugcStage2Error(
                exc.code,
                exc.message,
                status_code=exc.status_code,
            ) from exc
        bundle_sha256 = installed_stage2_skill_sha256(
            resolved_skill,
            settings=self.settings,
        )
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

        live_source_group = [
            member for member in source_group if member.status != "missing"
        ] or [plan]
        selected_source = random.choice(live_source_group)
        selected_source_snapshot = {
            "source_plan_id": selected_source.id,
            "remote_file_id": selected_source.source_file_id,
            "remote_folder_id": selected_source.source_parent_folder_id,
            "source_name": selected_source.source_name,
            "content_type": selected_source.source_mime_type,
            "size_bytes": selected_source.source_size_bytes,
            "source_revision": selected_source.source_revision,
        }
        effective_prompt = (prompt or "").strip() or DEFAULT_STAGE2_PROMPT

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

        run_ordinal = (
            self.session.query(RrugcStage2JobModel)
            .filter(
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.source_plan_id.in_(source_group_ids),
            )
            .count()
            + 1
        )
        raw_key = "|".join(
            [
                plan.id,
                plan.source_revision,
                f"run:{run_ordinal}",
                resolved.source,
                resolved.skill_id or "",
                resolved_skill,
                resolved.skill_version or "",
                bundle_sha256 or "",
                *ids,
                effective_prompt,
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
            skill_bundle_sha256=bundle_sha256,
            selected_candidate_ids_json=ids,
            selected_reference_snapshot_json=snapshot,
            selected_source_snapshot_json=selected_source_snapshot,
            prompt_text=effective_prompt,
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
                next_attempt_at=now + timedelta(seconds=STAGE2_CANCEL_GRACE_SECONDS),
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

    def regenerate_completed_job(
        self, *, tenant_id: str, user_id: str, job_id: str,
    ) -> RrugcStage2JobModel:
        """Generate from saved inputs using the latest installed version of this job skill."""
        original = self.session.scalar(select(RrugcStage2JobModel).where(
            RrugcStage2JobModel.tenant_id == tenant_id,
            RrugcStage2JobModel.id == job_id,
        ).with_for_update())
        if original is None:
            raise RrugcStage2Error("stage4_job_not_found", "Generation job not found.", status_code=404)
        # The router already requires RRUGC run permission and scopes the
        # lookup to this tenant. Do not impose another creator-only gate:
        # authorized viewers/editors need to retry shared generation work.
        _require_stage4_storage(self.settings)
        if original.status != "completed" or not original.output_remote_file_id:
            raise RrugcStage2Error("stage4_version_unavailable", "Only completed outputs can be regenerated.")

        root_id = original.regenerated_from_job_id or original.id
        # Every regeneration in the same lineage locks the same root row.
        # This prevents two concurrent requests from queuing duplicate work.
        root = self.session.scalar(select(RrugcStage2JobModel).where(
            RrugcStage2JobModel.tenant_id == tenant_id,
            RrugcStage2JobModel.id == root_id,
        ).with_for_update())
        if root is None:
            raise RrugcStage2Error("stage4_root_not_found", "Original generation was not found.", status_code=404)
        existing = self.session.scalar(select(RrugcStage2JobModel).where(
            RrugcStage2JobModel.tenant_id == tenant_id,
            or_(
                RrugcStage2JobModel.id == root_id,
                RrugcStage2JobModel.regenerated_from_job_id == root_id,
            ),
            RrugcStage2JobModel.status.in_(("queued", "running")),
        ).order_by(RrugcStage2JobModel.created_at.desc(), RrugcStage2JobModel.id.desc()).limit(1))
        if existing is not None:
            return existing

        # Reuse the exact original Drive hat and Pinterest snapshot. If a
        # mutable source has been replaced, fail visibly instead of queueing
        # a job that cannot reproduce the saved input.
        plan = self.session.get(RrugcSourcePlanModel, original.source_plan_id)
        if plan is None or plan.tenant_id != tenant_id or plan.source_revision != original.source_revision:
            raise RrugcStage2Error("stage4_source_changed",
                                   "The original hat source has changed; regeneration requires the saved source.", status_code=409)
        source_snapshot = dict(original.selected_source_snapshot_json or {})
        if source_snapshot:
            source_plan_id = str(source_snapshot.get("source_plan_id") or "")
            selected = self.session.get(RrugcSourcePlanModel, source_plan_id)
            if selected is None or selected.tenant_id != tenant_id or selected.source_revision != str(source_snapshot.get("source_revision") or ""):
                raise RrugcStage2Error("stage4_source_changed",
                                       "The selected hat source has changed; regeneration cannot reuse the original image.", status_code=409)

        ensure_skill_registry(self.session, tenant_id=tenant_id)
        try:
            assert_skill_enabled(self.session, tenant_id=tenant_id,
                                 source=original.skill_source, skill_id=original.skill_id,
                                 skill_name=original.skill_name)
        except Stage2SkillRegistryError as exc:
            raise RrugcStage2Error(exc.code, exc.message, status_code=exc.status_code) from exc
        try:
            current_skill = resolve_stage2_skill(
                settings=self.settings,
                skill_source=original.skill_source,
                skill_id=original.skill_id,
                skill_name=original.skill_name,
                skill_version=None,  # Regeneration uses the latest installed version.
                fallback_skill_name=DEFAULT_STAGE2_SKILL,
            )
        except Stage2SkillRegistryError as exc:
            raise RrugcStage2Error(exc.code, exc.message, status_code=exc.status_code) from exc
        current_bundle_sha256 = installed_stage2_skill_sha256(
            current_skill.skill_name, settings=self.settings
        )
        now = datetime.now(timezone.utc)
        row = RrugcStage2JobModel(
            tenant_id=tenant_id, source_plan_id=original.source_plan_id,
            campaign_id=original.campaign_id, source_revision=original.source_revision,
            regenerated_from_job_id=original.regenerated_from_job_id or original.id,
            skill_name=current_skill.skill_name, skill_source=current_skill.source,
            skill_id=current_skill.skill_id, skill_version=current_skill.skill_version,
            skill_bundle_sha256=current_bundle_sha256,
            selected_candidate_ids_json=list(original.selected_candidate_ids_json or []),
            selected_reference_snapshot_json=list(original.selected_reference_snapshot_json or []),
            selected_source_snapshot_json=dict(original.selected_source_snapshot_json or {}),
            prompt_text=original.prompt_text, status="queued",
            idempotency_key=hashlib.sha256(("rrugc-stage4-version:" + str(uuid4())).encode()).hexdigest(),
            queued_at=now, created_by_user_id=user_id,
        )
        self.session.add(row)
        self.session.flush()
        processing_job = ProcessingRepository(self.session, self.settings).create_job(
            tenant_id=tenant_id, job_type=STAGE2_JOB_TYPE,
            entity_type="rrugc_stage2_job", entity_id=row.id,
            idempotency_key=f"rrugc-stage2:{row.id}",
            payload={"stage2_job_id": row.id}, priority=25, max_attempts=3,
            next_attempt_at=now + timedelta(seconds=STAGE2_CANCEL_GRACE_SECONDS),
            provider_key="codex", provider_scope="ai",
        )
        row.processing_job_id = processing_job.id
        self.session.commit()
        self.session.refresh(row)
        return row

    def retry_failed_job(
        self, *, tenant_id: str, user_id: str, job_id: str,
    ) -> RrugcStage2JobModel:
        """Retry saved references using the current installed Skill replacement."""
        row = self.session.scalar(select(RrugcStage2JobModel).where(
            RrugcStage2JobModel.tenant_id == tenant_id,
            RrugcStage2JobModel.id == job_id,
        ).with_for_update())
        if row is None:
            raise RrugcStage2Error("stage4_job_not_found", "Generation job not found.", status_code=404)
        # RRUGC-run permission is enforced at the router. Same-tenant
        # operators can retry failed jobs created by other team members.
        _require_stage4_storage(self.settings)
        processing = self.session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
        if row.status != "failed" and (processing is None or processing.status != "failed"):
            raise RrugcStage2Error("stage4_retry_not_failed", "Only failed jobs can be retried.")
        previous = int(self.session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.entity_type == "rrugc_stage2_job",
            ProcessingJobModel.entity_id == job_id,
        )) or 0)
        try:
            latest = resolve_stage2_skill(
                settings=self.settings, skill_source=row.skill_source,
                skill_id=row.skill_id, skill_name=row.skill_name,
                skill_version=None, fallback_skill_name=DEFAULT_STAGE2_SKILL,
            )
        except Stage2SkillRegistryError as exc:
            raise RrugcStage2Error(exc.code, exc.message, status_code=exc.status_code) from exc
        row.skill_name = latest.skill_name
        row.skill_source = latest.source
        row.skill_id = latest.skill_id
        row.skill_version = latest.skill_version
        row.skill_bundle_sha256 = installed_stage2_skill_sha256(
            latest.skill_name, settings=self.settings
        )
        queued_at = datetime.now(timezone.utc)
        next_processing = ProcessingRepository(self.session, self.settings).create_job(
            tenant_id=tenant_id, job_type=STAGE2_JOB_TYPE,
            entity_type="rrugc_stage2_job", entity_id=row.id,
            idempotency_key=f"rrugc-stage2:{row.id}:retry:{previous}",
            payload={"stage2_job_id": row.id}, priority=25, max_attempts=3,
            next_attempt_at=queued_at, provider_key="codex", provider_scope="ai",
        )
        row.processing_job_id = next_processing.id
        row.status = "queued"
        row.last_error_code = None
        row.last_error_message = None
        row.started_at = None
        row.queued_at = queued_at
        self.session.commit()
        self.session.refresh(row)
        return row

    def cancel_recent_batch(
        self,
        *,
        tenant_id: str,
        user_id: str,
        source_plan_id: str,
        now: datetime | None = None,
        job_ids: list[str] | None = None,
    ) -> list[RrugcStage2JobModel]:
        plan = self.session.get(RrugcSourcePlanModel, source_plan_id)
        if plan is None or plan.tenant_id != tenant_id:
            raise RrugcStage2Error(
                "stage2_source_plan_not_found",
                "Embroidery source plan was not found.",
                status_code=404,
            )

        cancelled_at = now or datetime.now(timezone.utc)
        if cancelled_at.tzinfo is None:
            cancelled_at = cancelled_at.replace(tzinfo=timezone.utc)
        cancel_cutoff = cancelled_at - timedelta(seconds=STAGE2_CANCEL_GRACE_SECONDS)
        source_group_ids = [
            member.id
            for member in _stage2_source_group(
                self.session,
                tenant_id=tenant_id,
                plan=plan,
            )
        ]
        if job_ids is not None and (not job_ids or len(job_ids) > 250 or len(set(job_ids)) != len(job_ids)):
            raise RrugcStage2Error(
                "stage2_cancel_ids_invalid", "Select between 1 and 250 unique jobs to cancel.",
                status_code=422,
            )
        scoped = self.session.query(RrugcStage2JobModel).filter(
            RrugcStage2JobModel.tenant_id == tenant_id,
            RrugcStage2JobModel.source_plan_id.in_(source_group_ids),
            RrugcStage2JobModel.created_by_user_id == user_id,
        )
        if job_ids is not None:
            # Never silently ignore cross-tenant/cross-user targets, even if
            # some other IDs in the same batch are eligible to cancel.
            owned_count = scoped.filter(RrugcStage2JobModel.id.in_(job_ids)).count()
            if owned_count != len(job_ids):
                raise RrugcStage2Error(
                    "stage2_cancel_ids_invalid", "Some selected jobs are not owned by this user.",
                    status_code=409,
                )
            scoped = scoped.filter(RrugcStage2JobModel.id.in_(job_ids))
        rows = list(scoped.filter(
            RrugcStage2JobModel.status == "queued",
            RrugcStage2JobModel.queued_at.is_not(None),
            RrugcStage2JobModel.queued_at > cancel_cutoff,
        ).order_by(RrugcStage2JobModel.queued_at.asc()).with_for_update())
        # Old work is intentionally skipped. An older queued job should not
        # veto cancellation of a newer job from the same submitted batch.
        if not rows:
            self.session.rollback()
            raise RrugcStage2Error(
                "stage2_cancel_window_expired",
                "Stage 2 jobs can only be cancelled during the first 10 seconds.",
                status_code=409,
            )
        for row in rows:
            queued_at = row.queued_at
            if queued_at is not None and queued_at.tzinfo is None:
                queued_at = queued_at.replace(tzinfo=timezone.utc)
            if queued_at is None or queued_at <= cancel_cutoff:
                self.session.rollback()
                raise RrugcStage2Error(
                    "stage2_cancel_window_expired",
                    "Stage 2 jobs can only be cancelled during the first 10 seconds.",
                    status_code=409,
                )

        processing = ProcessingRepository(self.session, self.settings)
        cancelled: list[RrugcStage2JobModel] = []
        for row in rows:
            if not row.processing_job_id:
                self.session.rollback()
                raise RrugcStage2Error(
                    "stage2_cancel_unavailable",
                    "This Stage 2 batch has already started and can no longer be cancelled.",
                    status_code=409,
                )
            processing_job = processing.cancel_unstarted_job(
                tenant_id=tenant_id,
                job_id=row.processing_job_id,
                actor_id=user_id,
                reason="Stage 2 cancelled during 10-second grace period",
                now=cancelled_at,
            )
            if (
                processing_job is None
                or processing_job.last_error_code != "operation_cancelled"
            ):
                self.session.rollback()
                raise RrugcStage2Error(
                    "stage2_cancel_unavailable",
                    "This Stage 2 batch has already started and can no longer be cancelled.",
                    status_code=409,
                )
            row.status = "cancelled"
            row.completed_at = cancelled_at
            row.last_error_code = "stage2_cancelled"
            row.last_error_message = "Cancelled during the 10-second grace period."
            cancelled.append(row)

        self.session.commit()
        for row in cancelled:
            self.session.refresh(row)
        return cancelled


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
            # Defer rather than exhaust the processing retry budget during an
            # outage. Claim/deferral bookkeeping leaves the job in the queue.
            self._mark_failure(context, exc.code, str(exc), deferred=True)
            return DeferredJobOutcome(
                exc.code,
                str(exc),
                datetime.now(timezone.utc) + timedelta(
                    seconds=max(300, int(exc.defer_seconds or (900 if not exc.retryable else 300)))
                ),
            )
        except (RrugcGenerationHandlerError, StorageProviderError) as exc:
            code = getattr(exc, "code", "stage2_reference_unavailable")
            retryable = bool(getattr(exc, "retryable", True))
            self._mark_failure(context, code, str(exc), deferred=True)
            return DeferredJobOutcome(
                code, str(exc),
                datetime.now(timezone.utc) + timedelta(seconds=300 if retryable else 900),
            )
        except Exception as exc:
            self._mark_failure(context, "stage2_generation_internal_error", str(exc), deferred=True)
            context.logger.exception(
                "rrugc_stage2_generation_failed",
                extra={
                    "stage2_job_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                },
            )
            return DeferredJobOutcome(
                "stage2_generation_internal_error",
                "Stage 2 image generation failed.",
                datetime.now(timezone.utc) + timedelta(minutes=10),
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

        held = self._defer_if_paused(context, job_id)
        if held is not None:
            return held

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
                deferred=True,
            )
            return DeferredJobOutcome(
                "stage2_generation_disabled",
                "Codex image generation is disabled; job remains queued.",
                datetime.now(timezone.utc) + timedelta(minutes=15),
            )

        storage = context.dependencies.storage_provider
        # OAuth may have been reconnected after the worker started. Avoid a
        # worker restart if the credential is now available; never override
        # an explicitly supplied test provider.
        if settings.is_production and (storage is None or isinstance(storage, UnconfiguredAssetStorageProvider)):
            storage = build_managed_storage_provider(settings)
        if storage is None or isinstance(storage, UnconfiguredAssetStorageProvider):
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
            if row.status == "cancelled":
                return JobHandlerResult.non_retryable(
                    "stage2_cancelled",
                    "Stage 2 generation was cancelled during the grace period.",
                )
            plan = session.get(RrugcSourcePlanModel, row.source_plan_id)
            if plan is None or plan.source_revision != row.source_revision:
                row.status = "queued"
                row.last_error_code = "stage2_source_changed"
                row.last_error_message = "The embroidery source changed after this job was queued."
                session.commit()
                return DeferredJobOutcome(
                    "stage2_source_changed",
                    row.last_error_message,
                    datetime.now(timezone.utc) + timedelta(minutes=15),
                )
            try:
                verify_stage2_skill_runtime(
                    settings=settings,
                    skill_source=row.skill_source or "local",
                    skill_id=row.skill_id,
                    skill_name=row.skill_name,
                    skill_version=row.skill_version,
                )
                if row.skill_bundle_sha256:
                    current_bundle_sha256 = installed_stage2_skill_sha256(
                        row.skill_name,
                        settings=settings,
                    )
                    if current_bundle_sha256 != row.skill_bundle_sha256:
                        raise Stage2SkillRegistryError(
                            "stage2_skill_runtime_bundle_mismatch",
                            "The installed skill bundle changed after this job was queued.",
                            status_code=409,
                        )
            except Stage2SkillRegistryError as exc:
                row.status = "queued"
                row.last_error_code = exc.code
                row.last_error_message = exc.message
                session.commit()
                context.logger.error(
                    "rrugc_stage2_skill_pin_mismatch",
                    extra={
                        "stage2_job_id": job_id,
                        "tenant_id": context.job.tenant_id,
                        "skill_source": row.skill_source or "local",
                        "skill_id": row.skill_id,
                        "skill_name": row.skill_name,
                        "skill_version": row.skill_version,
                        "error_code": exc.code,
                    },
                )
                return DeferredJobOutcome(
                    exc.code, exc.message,
                    datetime.now(timezone.utc) + timedelta(minutes=15),
                )
            row.status = "running"
            row.started_at = row.started_at or datetime.now(timezone.utc)
            row.last_error_code = None
            row.last_error_message = None
            selected_source_snapshot = dict(row.selected_source_snapshot_json or {})
            if selected_source_snapshot:
                selected_source_plan_id = str(
                    selected_source_snapshot.get("source_plan_id") or ""
                )
                selected_source_plan = (
                    session.get(RrugcSourcePlanModel, selected_source_plan_id)
                    if selected_source_plan_id
                    else None
                )
                if (
                    selected_source_plan is None
                    or selected_source_plan.tenant_id != context.job.tenant_id
                    or selected_source_plan.source_revision
                    != str(selected_source_snapshot.get("source_revision") or "")
                ):
                    row.status = "queued"
                    row.last_error_code = "stage2_source_changed"
                    row.last_error_message = (
                        "The randomly selected hat input changed after this job was queued."
                    )
                    session.commit()
                    return DeferredJobOutcome(
                        "stage2_source_changed", row.last_error_message,
                        datetime.now(timezone.utc) + timedelta(minutes=15),
                    )
                source_file_id = str(
                    selected_source_snapshot.get("remote_file_id") or ""
                )
                source_mime_type = (
                    str(selected_source_snapshot.get("content_type"))
                    if selected_source_snapshot.get("content_type")
                    else None
                )
                source_size_bytes = selected_source_snapshot.get("size_bytes")
                source_parent_folder_id = str(
                    selected_source_snapshot.get("remote_folder_id")
                    or selected_source_plan.source_parent_folder_id
                    or ""
                )
                source_input_plan_id = selected_source_plan_id
                source_input_name = str(
                    selected_source_snapshot.get("source_name") or ""
                )
            else:
                # Backward compatibility for jobs queued before source snapshots existed.
                source_file_id = plan.source_file_id
                source_mime_type = plan.source_mime_type
                source_size_bytes = plan.source_size_bytes
                source_parent_folder_id = str(plan.source_parent_folder_id or "")
                source_input_plan_id = plan.id
                source_input_name = plan.source_name
            references = list(row.selected_reference_snapshot_json or [])
            skill_name = row.skill_name
            skill_source = row.skill_source or "local"
            skill_id = row.skill_id
            skill_version = row.skill_version
            prompt = row.prompt_text or ""
            session.commit()

        context.logger.info(
            "rrugc_stage2_generation_started",
            extra={
                "stage2_job_id": job_id,
                "tenant_id": context.job.tenant_id,
                "skill_source": skill_source,
                "skill_id": skill_id,
                "skill_name": skill_name,
                "skill_version": skill_version,
                "reference_count": len(references[:MAX_STAGE2_REFERENCES]),
                "source_input_plan_id": source_input_plan_id,
                "source_input_name": source_input_name,
            },
        )

        source = await self._open_prepared(
            storage,
            tenant_id=context.job.tenant_id,
            asset_id=f"rrugc-stage2-source:{job_id}:{source_input_plan_id}",
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

        # A pause requested while Drive inputs were loading must stop before
        # dispatching a costly image-generation request. Existing outputs remain
        # untouched; this is a defer, not a cancellation or failed retry.
        held = self._defer_if_paused(context, job_id)
        if held is not None:
            return held

        generation_prompt = (prompt or DEFAULT_STAGE2_PROMPT).strip()
        primary_home = str(getattr(settings, "CODEX_IMAGE_HOME", "/var/lib/creative-asset-manager/codex"))
        binary = str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex")
        with context.dependencies.session_factory() as pool_session:
            try:
                with acquire_worker_profile(
                    pool_session, context.job.tenant_id, "stage4",
                    primary_home, binary, skill_name,
                ) as (_account_id, selected_home):
                    pool_session.commit()
                    context.logger.info("rrugc_codex_pool_dispatch", extra={
                        "stage": "stage4", "account_id": _account_id,
                        "job_id": job_id, "tenant_id": context.job.tenant_id,
                    })
                    runner = CodexImageGenRunner(
                        CodexImageRunnerConfig(
                            binary=binary,
                            codex_home=selected_home,
                            staging_root=str(getattr(
                                settings, "IMAGE_GENERATION_STAGING_ROOT",
                                "/var/lib/creative-asset-manager/image-generation",
                            )),
                            skill_name=skill_name,
                            timeout_seconds=int(getattr(settings, "CODEX_IMAGE_TIMEOUT_SECONDS", 900)),
                            model=str(getattr(settings, "CODEX_IMAGE_MODEL", "")).strip() or None,
                        )
                    )
                    try:
                        result = await runner.generate_from_references(
                            attempt_id=job_id,
                            person=source,
                            references=reference_inputs,
                            prompt=generation_prompt,
                        )
                    finally:
                        runner.cleanup_attempt(job_id)
            except CodexAccountPoolBusy:
                self._mark_failure(context, "codex_account_pool_busy",
                                   "All enabled Codex profiles are busy.", deferred=True)
                return DeferredJobOutcome(
                    "codex_account_pool_busy",
                    "All enabled Codex worker profiles are busy.",
                    datetime.now(timezone.utc) + timedelta(seconds=60),
                )

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
                asset_id=f"rrugc-stage2:{job_id}:{context.job.id}",
                content_type=result.mime_type,
                size_bytes=len(result.image_bytes),
                filename=f"output_{job_id}_{context.job.id}{_output_extension(result.mime_type)}",
                destination_folder_id=source_parent_folder_id or None,
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
            save_output_version(
                session, tenant_id=context.job.tenant_id, stage="stage4", job=row,
                remote_file_id=stored.remote_file_id, content_type=result.mime_type,
                size_bytes=len(result.image_bytes), width=width, height=height,
                processing_job_id=context.job.id,
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
            RrugcStage3Service(session).ensure_analysis_for_job(
                tenant_id=context.job.tenant_id,
                stage2_job=row,
            )
            session.commit()

        context.logger.info(
            "rrugc_stage2_generation_completed",
            extra={
                "stage2_job_id": job_id,
                "tenant_id": context.job.tenant_id,
                "skill_source": skill_source,
                "skill_id": skill_id,
                "skill_name": skill_name,
                "skill_version": skill_version,
                "reference_count": len(reference_inputs),
                "output_width": width,
                "output_height": height,
            },
        )
        return JobHandlerResult.completed()

    @staticmethod
    def _defer_if_paused(
        context: JobHandlerContext, job_id: str,
    ) -> DeferredJobOutcome | None:
        from app.modules.realistic_review_ugc.stage4_queue_control import stage4_is_paused

        with context.dependencies.session_factory() as session:
            if not stage4_is_paused(session, context.job.tenant_id):
                return None
            row = RrugcRepository(session).lock_stage2_job(context.job.tenant_id, job_id)
            if row is not None and row.status == "running":
                row.status = "queued"
                session.commit()
        return DeferredJobOutcome(
            "stage4_paused", "Stage 4 generation paused by an operator.",
            datetime.now(timezone.utc) + timedelta(seconds=60),
        )

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
        deferred: bool = False,
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
            if not terminal and not deferred:
                # ClaimedJob intentionally has no max_attempts attribute.
                # Consult the authoritative durable processing row instead.
                processing = session.get(ProcessingJobModel, context.job.id)
                terminal = bool(
                    processing is not None
                    and processing.attempt_count >= processing.max_attempts
                )
            row.status = "failed" if terminal else "queued"
            row.last_error_code = str(code)[:100]
            row.last_error_message = str(message)[:1000]
            session.commit()