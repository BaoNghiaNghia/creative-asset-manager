"""Independent, pinned Ecommerce single-image jobs for four GatorHats 8869 views."""
from __future__ import annotations

import asyncio
import hashlib
import httpx
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
from app.modules.realistic_review_ugc.hat_stock_sync import COLORS
from app.modules.realistic_review_ugc.ecommerce_front_reference import FrontReferenceError, render_front_reference
from app.modules.realistic_review_ugc.embroidery_realism import embroidery_prompt_for_product_slot
from app.modules.realistic_review_ugc.generation_handler import (
    RrugcGenerationHandlerError, _bytes_body, _output_extension, _prepared_image,
    MAX_REFERENCE_BYTES, MAX_REFERENCE_PIXELS,
)
from app.modules.realistic_review_ugc.model import (
    RrugcEcommerceJobModel, RrugcSourcePlanModel, RrugcStageSkillDefaultModel,
    RrugcStage2SkillRegistryModel, RrugcCandidateModel,
)
from app.modules.realistic_review_ugc.output_versions import save_output_version
from app.modules.realistic_review_ugc.ugc_scene_selection import select_ugc_references, allowed_scout_image_url
from app.modules.realistic_review_ugc.multihat_scene_references import (
    MultiHatReferencesUnavailable, build_multihat_reference_sheets,
    select_multihat_references, validate_pinned_multihat_references,
)
from app.modules.realistic_review_ugc.multihat_colorways import (
    MultiHatColorwayError, STAGE_CAP_COUNTS, COLOR_NAMES,
    available_stock_colors, choose_palettes, validated_palette,
    pinned_stock_hashes, checked_palette_stock, make_palette_board,
    multihat_color_prompt, different_regeneration_palette,
)
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillRegistryError, resolve_stage2_skill,
    verify_stage2_skill_runtime, installed_stage2_skill_sha256,
)
from app.modules.realistic_review_ugc.skill_registry import assert_skill_enabled
from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError

JOB_TYPE = "rrugc_ecommerce_generate"
# Reserve an interactive lane above bulk index backlog, without bypassing policies.
ECOMMERCE_INTERACTIVE_PRIORITY = 96
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
        "stitch structure and the five-panel crown. No design outside the guide area. Preserve the exact GatorHats side wordmark on the cap side if it is naturally visible."
    ),
    "prod_s2_ec2": (
        "FRONT RIGHT THREE-QUARTER high catalog photo. Rotate the exact Valucap 8869 "
        "five-panel cap from the official frontal view toward the right. Use the separate "
        "right-side stock photo ONLY as evidence of its side geometry, not as a 3/4 photo. "
        "Preserve the already embroidered front-reference design, scaling and stitch colors "
        "with correct perspective on the FRONT crown. The approved product has small red GatorHats embroidered side branding near the lower side seam; show it naturally if visible. Do not invent other text."
    ),
    "prod_s2_ec3": (
        "TRUE RIGHT SIDE 90 degree high catalog photo. Match the supplied exact stock "
        "right-side geometry and cap colors. Use the embroidered front reference for "
        "continuity; only show portions of the front embroidery visible naturally "
        "from this angle. Apply small authentic-looking red GatorHats embroidery on the lateral crown near the side seam, matching the reference product's side branding. No new slogans or other text."
    ),
    "prod_s2_ec4": (
        "TRUE BACK high catalog photo. Preserve the precise rear view from the official "
        "stock cap photograph. Keep the rear panel blank and preserve only the small "
        "GatorHats logo naturally visible at the far side edge in the approved product. "
        "Do not place text across the rear crown. Show the rear crown and adjustable strap faithfully."
    ),
}

# Scene shots deliberately reuse the proven Ecommerce job persistence, retry,
# storage/version and image-worker contract. A unique stage identifies EACH
# output, while the tenant Skill assignment remains at the parent slot.
SCENE_GROUPS = {
    "prod_s2_mh": ("prod_s2_mh_1", "prod_s2_mh_2", "prod_s2_mh_3"),
    "prod_s2_ugc": ("prod_s2_ugc_1", "prod_s2_ugc_2", "prod_s2_ugc_3"),
}
SCENE_STAGE_SLOTS = {
    shot: slot for slot, shots in SCENE_GROUPS.items() for shot in shots
}
SCENE_SHOT_DESCRIPTIONS = {
    "prod_s2_mh_1": "Four official Valucap 8869 caps arranged on a neutral tabletop, one hero front and three staggered companions.",
    "prod_s2_mh_2": "Four official Valucap 8869 caps in a carefully lit lifestyle arrangement, with one front-facing hero and visible side profiles.",
    "prod_s2_mh_3": "Three official Valucap 8869 caps in a clean editorial display showing front, three-quarter and side orientation.",
    "prod_s2_ugc_1": "Natural handheld customer photograph of a person wearing the official five-panel cap outdoors.",
    "prod_s2_ugc_2": "Distinct casual customer selfie of a person wearing the official five-panel cap in natural daylight.",
    "prod_s2_ugc_3": "Distinct authentic lifestyle photograph featuring a person wearing the official five-panel cap in an everyday setting.",
}
for _stage in SCENE_STAGE_SLOTS:
    STOCK_ROLES[_stage] = ("stock_front", "stock_side_90", "stock_back")
    VIEW_PROMPTS[_stage] = (
        SCENE_SHOT_DESCRIPTIONS[_stage] +
        " Follow the assigned uploaded Skill's composition and quality requirements. "
        "Use the EXACT provided embroidery design where the cap front is visible, "
        "with physically realistic stitched thread and lettering. Keep the "
        "Valucap 8869 five-panel single-front crown, crown seams, visor, color "
        "and factory stock geometry authentic. No invented logos, altered words, "
        "six-panel seam, random side embroidery, extra people or extra caps "
        "outside the scene instructions. Produce exactly ONE complete image "
        "for this independent shot; never a collage or contact sheet."
    )


class EcommerceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _stock_refs(settings: Settings, stage: str, color_key: str = "natural-black") -> tuple[list[tuple[str, bytes]], str]:
    if color_key not in COLORS:
        raise EcommerceError("ecommerce_color_invalid", "Unknown hat color.", 422)
    if stage not in STOCK_ROLES:
        raise EcommerceError("ecommerce_stage_invalid", "Unknown Ecommerce photo slot.", 422)
    # The checkout root is also available in the immutable production release.
    project_root = Path(__file__).resolve().parents[5]
    from app.modules.realistic_review_ugc.hat_stock_sync import stock_read_lock
    try:
        with stock_read_lock(settings):
            refs = [(role, read_release_stock(role, project_root=project_root, color_key=color_key)[0])
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
    assignment = session.get(RrugcStageSkillDefaultModel, (tenant_id, SCENE_STAGE_SLOTS.get(stage, stage)))
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


def _front_job(session: Session, plan: RrugcSourcePlanModel, color_key: str = "natural-black") -> RrugcEcommerceJobModel:
    front = session.scalar(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.tenant_id == plan.tenant_id,
        RrugcEcommerceJobModel.source_plan_id == plan.id,
        RrugcEcommerceJobModel.source_revision == plan.source_revision,
        RrugcEcommerceJobModel.stage == "prod_s2_ec1",
        RrugcEcommerceJobModel.color_key == color_key,
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

    def _saved_multihat_palettes(self, *, tenant_id: str, plan: RrugcSourcePlanModel,
                                color_key: str) -> dict[str, list[str]]:
        """Read already pinned stage palettes; never change completed/active jobs."""
        rows = self.session.scalars(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.tenant_id == tenant_id,
            RrugcEcommerceJobModel.source_plan_id == plan.id,
            RrugcEcommerceJobModel.source_revision == plan.source_revision,
            RrugcEcommerceJobModel.stage.in_(SCENE_GROUPS["prod_s2_mh"]),
            RrugcEcommerceJobModel.color_key == color_key,
        ))
        result: dict[str, list[str]] = {}
        for existing in rows:
            processing = self.session.get(ProcessingJobModel, existing.processing_job_id) if existing.processing_job_id else None
            value = (processing.payload_json or {}).get("multihat_colorways") if processing else None
            selected = validated_palette(existing.stage, value)
            if selected:
                result[existing.stage] = selected
        return result

    def _choose_multihat_palettes(self, *, tenant_id: str, plan: RrugcSourcePlanModel,
                                  color_key: str) -> dict[str, list[str]]:
        try:
            return choose_palettes(
                tenant_id=tenant_id, source_plan_id=plan.id,
                source_revision=plan.source_revision,
                available=available_stock_colors(self.settings),
                pinned=self._saved_multihat_palettes(
                    tenant_id=tenant_id, plan=plan, color_key=color_key),
            )
        except MultiHatColorwayError as exc:
            raise EcommerceError("multihat_colorways_unavailable", str(exc), 409) from exc

    def _multihat_stock_snapshot(self, stage: str, palette: object) -> tuple[list[str], dict[str, str]]:
        selected = validated_palette(stage, palette)
        if not selected:
            raise EcommerceError("multihat_colorways_invalid",
                                 "Choose distinct official hat colors for each cap.", 409)
        try:
            return selected, pinned_stock_hashes(self.settings, stage, selected)
        except MultiHatColorwayError as exc:
            raise EcommerceError("multihat_stock_unavailable", str(exc), 503) from exc

    def _enqueue(self, row: RrugcEcommerceJobModel, *, scout_reference=None, multihat_references=None,
                 multihat_colorways=None, multihat_colorway_hashes=None) -> None:
        count = int(self.session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == row.tenant_id,
            ProcessingJobModel.entity_type == "rrugc_ecommerce_job",
            ProcessingJobModel.entity_id == row.id,
        )) or 0)
        processing = ProcessingRepository(self.session, self.settings).create_job(
            tenant_id=row.tenant_id, job_type=JOB_TYPE, entity_type="rrugc_ecommerce_job",
            entity_id=row.id, idempotency_key=f"rrugc-ecommerce:{row.id}:{count}",
            payload={
                "ecommerce_job_id": row.id,
                **({"scout_reference": scout_reference} if scout_reference else {}),
                **({"multihat_references": multihat_references} if multihat_references else {}),
                **({"multihat_colorways": multihat_colorways,
                    "multihat_colorway_hashes": multihat_colorway_hashes}
                   if multihat_colorways else {}),
            }, priority=20, max_attempts=3,
            provider_key="codex", provider_scope="ai",
        )
        processing.priority = ECOMMERCE_INTERACTIVE_PRIORITY
        row.processing_job_id = processing.id

    def queue(self, *, tenant_id: str, user_id: str, source_plan_id: str,
              stage: str, color_key: str = "natural-black", scout_reference=None, multihat_references=None,
              multihat_colorways=None, multihat_colorway_hashes=None) -> dict:
        if stage not in SHOTS_BY_STAGE and stage not in SCENE_STAGE_SLOTS:
            raise EcommerceError("ecommerce_stage_invalid", "Unknown photo skill slot.", 422)
        if not all((
            self.settings.PROCESSING_JOBS_ENABLED,
            self.settings.IMAGE_GENERATION_ENABLED,
            self.settings.MANAGED_ASSET_STORAGE_ENABLED,
            self.settings.CODEX_IMAGE_GENERATION_ENABLED,
        )):
            raise EcommerceError("ecommerce_execution_disabled", "Image generation is disabled.", 503)
        plan = _source_plan(self.session, tenant_id, source_plan_id, lock=True)
        _refs, stock_digest = _stock_refs(self.settings, stage, color_key)
        skill = _assigned_skill(self.session, self.settings, tenant_id, stage)
        front = _front_job(self.session, plan, color_key) if stage in ("prod_s2_ec2", "prod_s2_ec3") else None
        row = self.session.scalar(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.tenant_id == tenant_id,
            RrugcEcommerceJobModel.source_plan_id == source_plan_id,
            RrugcEcommerceJobModel.source_revision == plan.source_revision,
            RrugcEcommerceJobModel.stage == stage,
            RrugcEcommerceJobModel.color_key == color_key,
        ).with_for_update())
        if row is not None:
            return {"job_id": row.id, "status": row.status, "already_exists": True}
        if stage in SCENE_GROUPS["prod_s2_mh"]:
            multihat_references = validate_pinned_multihat_references(multihat_references)
            if not multihat_references:
                siblings = self.session.scalars(select(RrugcEcommerceJobModel).where(
                    RrugcEcommerceJobModel.tenant_id == tenant_id,
                    RrugcEcommerceJobModel.source_plan_id == plan.id,
                    RrugcEcommerceJobModel.source_revision == plan.source_revision,
                    RrugcEcommerceJobModel.stage.in_(SCENE_GROUPS["prod_s2_mh"]),
                    RrugcEcommerceJobModel.color_key == color_key,
                ))
                for sibling in siblings:
                    previous = (self.session.get(ProcessingJobModel, sibling.processing_job_id)
                                if sibling.processing_job_id else None)
                    multihat_references = validate_pinned_multihat_references(
                        (previous.payload_json or {}).get("multihat_references") if previous else None)
                    if multihat_references:
                        break
            if not multihat_references:
                try:
                    multihat_references = select_multihat_references(
                        self.session, tenant_id=tenant_id,
                        source_plan_id=plan.id, source_revision=plan.source_revision,
                    )
                except MultiHatReferencesUnavailable as exc:
                    raise EcommerceError("multihat_references_missing", str(exc), 409) from exc
        if stage in SCENE_GROUPS["prod_s2_mh"]:
            if multihat_colorways is None:
                multihat_colorways = self._choose_multihat_palettes(
                    tenant_id=tenant_id, plan=plan, color_key=color_key)[stage]
            selected, verified_hashes = self._multihat_stock_snapshot(stage, multihat_colorways)
            if multihat_colorway_hashes is not None and verified_hashes != multihat_colorway_hashes:
                raise EcommerceError("multihat_stock_changed",
                                     "Official Multi-hat stock changed before queueing.")
            multihat_colorways, multihat_colorway_hashes = selected, verified_hashes
        row = RrugcEcommerceJobModel(
            id=str(uuid4()), tenant_id=tenant_id, source_plan_id=source_plan_id,
            source_revision=plan.source_revision, stage=stage, color_key=color_key,
            stock_sha256=stock_digest,
            front_reference_remote_file_id=front.output_remote_file_id if front else None,
            front_reference_content_type=front.output_content_type if front else None,
            front_reference_size_bytes=front.output_size_bytes if front else None,
            front_preview_remote_file_id=front.front_preview_remote_file_id if front else None,
            front_preview_content_type=front.front_preview_content_type if front else None,
            front_preview_size_bytes=front.front_preview_size_bytes if front else None,
            front_preview_design_sha256=front.front_preview_design_sha256 if front else None,
            skill_source=skill.source,
            skill_id=skill.skill_id, skill_name=skill.skill_name,
            skill_version=skill.skill_version,
            skill_bundle_sha256=installed_stage2_skill_sha256(skill.skill_name, settings=self.settings),
            created_by_user_id=user_id, status="queued",
        )
        self.session.add(row)
        self.session.flush()
        self._enqueue(row, scout_reference=scout_reference, multihat_references=multihat_references,
                      multihat_colorways=multihat_colorways,
                      multihat_colorway_hashes=multihat_colorway_hashes)
        self.session.commit()
        return {"job_id": row.id, "status": row.status, "already_exists": False}

    def queue_scene(self, *, tenant_id: str, user_id: str,
                    source_plan_id: str, slot: str,
                    color_key: str = "natural-black") -> dict:
        """Queue exactly three independent, idempotent photos for one Skill.

        Missing/failed shots can be resumed without duplicating completed shots.
        A single invalid Skill or missing official stock blocks BEFORE queueing.
        """
        stages = SCENE_GROUPS.get(slot)
        if not stages:
            raise EcommerceError("photo_slot_invalid", "Unknown three-photo Skill slot.", 422)
        plan = _source_plan(self.session, tenant_id, source_plan_id)
        _assigned_skill(self.session, self.settings, tenant_id, slot)
        for stage in stages:
            _stock_refs(self.settings, stage, color_key)
        scout_refs = {}
        multihat_refs: list[dict] = []
        multihat_palettes: dict[str, list[str]] = {}
        if slot == "prod_s2_mh":
            existing_rows = list(self.session.scalars(
                select(RrugcEcommerceJobModel).where(
                    RrugcEcommerceJobModel.tenant_id == tenant_id,
                    RrugcEcommerceJobModel.source_plan_id == source_plan_id,
                    RrugcEcommerceJobModel.source_revision == plan.source_revision,
                    RrugcEcommerceJobModel.stage.in_(stages),
                    RrugcEcommerceJobModel.color_key == color_key,
                )))
            unpinned = False
            unpinned_colors = False
            for existing in existing_rows:
                processing = self.session.get(ProcessingJobModel, existing.processing_job_id) if existing.processing_job_id else None
                pinned = validate_pinned_multihat_references(
                    (processing.payload_json or {}).get("multihat_references") if processing else None)
                if pinned and not multihat_refs:
                    multihat_refs = pinned
                if (processing and processing.status == "pending"
                        and processing.attempt_count == 0 and not pinned):
                    unpinned = True
                if (processing and processing.status == "pending"
                        and processing.attempt_count == 0
                        and not validated_palette(existing.stage, (
                            processing.payload_json or {}).get("multihat_colorways"))):
                    unpinned_colors = True
            if (len(existing_rows) < len(stages)
                    or any(_effective_status(row, (
                        self.session.get(ProcessingJobModel, row.processing_job_id)
                        if row.processing_job_id else None
                    )) == "failed" for row in existing_rows) or unpinned):
                if not multihat_refs:
                    try:
                        multihat_refs = select_multihat_references(
                            self.session, tenant_id=tenant_id, source_plan_id=plan.id,
                            source_revision=plan.source_revision,
                        )
                    except MultiHatReferencesUnavailable as exc:
                        raise EcommerceError("multihat_references_missing", str(exc), 409) from exc
            if (len(existing_rows) < len(stages)
                    or any(_effective_status(row, (
                        self.session.get(ProcessingJobModel, row.processing_job_id)
                        if row.processing_job_id else None
                    )) == "failed" for row in existing_rows) or unpinned_colors):
                multihat_palettes = self._choose_multihat_palettes(
                    tenant_id=tenant_id, plan=plan, color_key=color_key)
        if slot == "prod_s2_ugc":
            if not plan.campaign_id:
                raise EcommerceError("ugc_scout_campaign_missing", "No assigned Scout campaign.", 409)
            related = list(self.session.scalars(select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.tenant_id == tenant_id,
                RrugcSourcePlanModel.campaign_id == plan.campaign_id,
                RrugcSourcePlanModel.status != "missing")))
            if len(related) > 1 and (not plan.embroidery_signature or any(
                    other.embroidery_signature != plan.embroidery_signature
                    for other in related if other.id != plan.id)):
                raise EcommerceError("ugc_scout_campaign_shared",
                    "Scout campaign includes different embroidery designs.", 409)
            existing_rows = list(self.session.scalars(
                select(RrugcEcommerceJobModel).where(
                    RrugcEcommerceJobModel.tenant_id == tenant_id,
                    RrugcEcommerceJobModel.source_plan_id == source_plan_id,
                    RrugcEcommerceJobModel.source_revision == plan.source_revision,
                    RrugcEcommerceJobModel.stage.in_(stages),
                    RrugcEcommerceJobModel.color_key == color_key)))
            existing_stages = {item.stage for item in existing_rows}
            unpinned = any(
                item.status != "completed" and not (
                    (self.session.get(ProcessingJobModel, item.processing_job_id).payload_json or {})
                    .get("scout_reference")
                ) for item in existing_rows if item.processing_job_id and
                self.session.get(ProcessingJobModel, item.processing_job_id))
            if len(existing_stages) < len(stages) or unpinned:
                candidates = list(self.session.scalars(
                    select(RrugcCandidateModel).where(
                        RrugcCandidateModel.tenant_id == tenant_id,
                        RrugcCandidateModel.campaign_id == plan.campaign_id,
                    ).order_by(RrugcCandidateModel.final_score.desc().nullslast(),
                               RrugcCandidateModel.created_at.desc()).limit(150)))
                selected = select_ugc_references(
                    candidates, selection_key=(
                        f"{tenant_id}:{plan.id}:{plan.source_revision}:{color_key}"
                    ),
                )
                if len(selected) != 3:
                    raise EcommerceError("ugc_scout_references_missing",
                        "Need three approved and distinct Scout images.", 409)
                scout_refs = {
                    stage: {"candidate_id": c.id, "campaign_id": plan.campaign_id,
                            "image_url": c.image_url, "content_hash": c.content_hash}
                    for stage, c in zip(stages, selected)
                }
        results = []
        for stage in stages:
            try:
                existing = self.session.scalar(select(RrugcEcommerceJobModel).where(
                    RrugcEcommerceJobModel.tenant_id == tenant_id,
                    RrugcEcommerceJobModel.source_plan_id == source_plan_id,
                    RrugcEcommerceJobModel.source_revision == plan.source_revision,
                    RrugcEcommerceJobModel.stage == stage,
                    RrugcEcommerceJobModel.color_key == color_key,
                ))
                if existing is None:
                    queued = self.queue(tenant_id=tenant_id, user_id=user_id,
                                        source_plan_id=source_plan_id, stage=stage,
                                        scout_reference=scout_refs.get(stage),
                                        multihat_references=multihat_refs,
                                        multihat_colorways=multihat_palettes.get(stage),
                                        color_key=color_key)
                    results.append({"stage": stage, **queued})
                    continue
                processing = (self.session.get(ProcessingJobModel, existing.processing_job_id)
                              if existing.processing_job_id else None)
                status = _effective_status(existing, processing)
                reference = scout_refs.get(stage)
                if (multihat_palettes.get(stage) and processing and processing.status == "pending"
                        and processing.attempt_count == 0
                        and not validated_palette(stage, (processing.payload_json or {}).get("multihat_colorways"))):
                    chosen, digests = self._multihat_stock_snapshot(
                        stage, multihat_palettes[stage])
                    processing.payload_json = {
                        **(processing.payload_json or {}),
                        "multihat_colorways": chosen,
                        "multihat_colorway_hashes": digests,
                    }
                    self.session.commit()
                if (multihat_refs and processing and processing.status == "pending"
                        and processing.attempt_count == 0
                        and not validate_pinned_multihat_references(
                            (processing.payload_json or {}).get("multihat_references"))):
                    processing.payload_json = {
                        **(processing.payload_json or {}),
                        "multihat_references": multihat_refs,
                    }
                    self.session.commit()
                if (reference and processing and processing.status == "pending"
                        and processing.attempt_count == 0
                        and not (processing.payload_json or {}).get("scout_reference")):
                    processing.payload_json = {**(processing.payload_json or {}), "scout_reference": reference}
                    self.session.commit()
                if status == "failed" and existing.retry_count < MAX_MANUAL_RETRIES:
                    queued = self.retry(tenant_id=tenant_id, job_id=existing.id,
                                        scout_reference=reference, multihat_references=multihat_refs,
                                        multihat_colorways=multihat_palettes.get(stage))
                    results.append({"stage": stage, **queued})
                elif status == "failed":
                    results.append({"stage": stage, "job_id": existing.id,
                                    "status": "blocked", "reason": "Retry limit reached."})
                else:
                    results.append({"stage": stage, "job_id": existing.id,
                                    "status": status, "already_exists": True})
            except EcommerceError as exc:
                self.session.rollback()
                results.append({"stage": stage, "status": "blocked",
                                "reason": exc.message, "error_code": exc.code})
        return {"source_plan_id": source_plan_id, "slot": slot, "steps": results}

    def queue_all(self, *, tenant_id: str, user_id: str, source_plan_id: str,
                  color_key: str = "natural-black") -> dict:
        """Start every supported Ecommerce slot with durable Front -> angle handoff.

        Jobs 2 and 3 cannot be queued until Front output has been saved.  The
        intent is pinned to the Front processing payload and completed by the
        image worker, even when the user's browser is closed.  Other slots are
        independent and failures are isolated per slot.
        """
        plan = _source_plan(self.session, tenant_id, source_plan_id)
        if not all((self.settings.PROCESSING_JOBS_ENABLED,
                    self.settings.IMAGE_GENERATION_ENABLED,
                    self.settings.MANAGED_ASSET_STORAGE_ENABLED,
                    self.settings.CODEX_IMAGE_GENERATION_ENABLED)):
            return {"source_plan_id": source_plan_id, "steps": [
                {"stage": stage, "status": "blocked",
                 "reason": "Image generation is disabled."}
                for stage in ("prod_s2_ec1", "prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4")]}
        stages: dict[str, dict] = {}
        front = None

        def enqueue(stage: str) -> dict:
            existing = self.session.scalar(select(RrugcEcommerceJobModel).where(
                RrugcEcommerceJobModel.tenant_id == tenant_id,
                RrugcEcommerceJobModel.source_plan_id == source_plan_id,
                RrugcEcommerceJobModel.source_revision == plan.source_revision,
                RrugcEcommerceJobModel.stage == stage,
                RrugcEcommerceJobModel.color_key == color_key,
            ))
            if existing is not None:
                processing = (self.session.get(ProcessingJobModel, existing.processing_job_id)
                              if existing.processing_job_id else None)
                state = _effective_status(existing, processing)
                if state == "failed":
                    if existing.retry_count >= MAX_MANUAL_RETRIES:
                        raise EcommerceError("ecommerce_retry_exhausted",
                                             "Job retry limit reached.")
                    result = self.retry(tenant_id=tenant_id, job_id=existing.id)
                    return {"stage": stage, "status": "queued", "job_id": result["job_id"]}
                return {"stage": stage, "status": state, "job_id": existing.id}
            queued = self.queue(tenant_id=tenant_id, user_id=user_id,
                                source_plan_id=source_plan_id, stage=stage, color_key=color_key)
            return {"stage": stage, "status": queued["status"],
                    "job_id": queued["job_id"]}

        # Front and Back never require another Ecommerce output.
        for stage in ("prod_s2_ec1", "prod_s2_ec4"):
            try:
                stages[stage] = enqueue(stage)
            except EcommerceError as exc:
                self.session.rollback()
                stages[stage] = {"stage": stage, "status": "blocked",
                                 "reason": exc.message, "error_code": exc.code}

        front = self.session.scalar(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.tenant_id == tenant_id,
            RrugcEcommerceJobModel.source_plan_id == source_plan_id,
            RrugcEcommerceJobModel.source_revision == plan.source_revision,
            RrugcEcommerceJobModel.stage == "prod_s2_ec1",
            RrugcEcommerceJobModel.color_key == color_key,
        ))

        deferred = []
        for stage in ("prod_s2_ec2", "prod_s2_ec3"):
            try:
                if front and front.status == "completed" and front.output_remote_file_id:
                    stages[stage] = enqueue(stage)
                else:
                    # Validate stock and assigned Skill *before* marking a
                    # dependent task as waiting; never represent absent
                    # dependencies as queued jobs.
                    _stock_refs(self.settings, stage, color_key)
                    _assigned_skill(self.session, self.settings, tenant_id, stage)
                    if not front or front.status == "failed":
                        raise EcommerceError("ecommerce_front_prerequisite",
                                             "Front High must run successfully first.")
                    deferred.append(stage)
                    stages[stage] = {"stage": stage, "status": "waiting_for_front",
                                     "reason": "Will queue after Ecommerce Image 01 completes."}
            except EcommerceError as exc:
                self.session.rollback()
                stages[stage] = {"stage": stage, "status": "blocked",
                                 "reason": exc.message, "error_code": exc.code}

        if deferred and front and front.processing_job_id:
            # Lock the same Front row that the completion handler updates, so
            # a concurrent completion cannot race past the durable intent.
            front = self.session.scalar(select(RrugcEcommerceJobModel).where(
                RrugcEcommerceJobModel.id == front.id,
                RrugcEcommerceJobModel.tenant_id == tenant_id,
            ).with_for_update())
            if front and front.status == "completed" and front.output_remote_file_id:
                self.session.rollback()
                for stage in deferred:
                    try:
                        stages[stage] = enqueue(stage)
                    except EcommerceError as exc:
                        self.session.rollback()
                        stages[stage] = {"stage": stage, "status": "blocked",
                                         "reason": exc.message, "error_code": exc.code}
            else:
                processing = self.session.scalar(select(ProcessingJobModel).where(
                    ProcessingJobModel.id == front.processing_job_id,
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.entity_id == front.id,
                    ProcessingJobModel.job_type == JOB_TYPE,
                ).with_for_update())
                if processing is None:
                    self.session.rollback()
                    for stage in deferred:
                        stages[stage] = {"stage": stage, "status": "blocked",
                                         "reason": "Front processing job is unavailable."}
                else:
                    current = dict(processing.payload_json or {})
                    previous = current.get("run_all_followups", [])
                    current["run_all_followups"] = sorted(set(previous).union(deferred))
                    current["run_all_user_id"] = user_id
                    current["run_all_color_key"] = color_key
                    processing.payload_json = current
                    self.session.commit()

        return {"source_plan_id": source_plan_id,
                "steps": [stages[key] for key in (
                    "prod_s2_ec1", "prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4")]}

    def retry(self, *, tenant_id: str, job_id: str, scout_reference=None,
              multihat_references=None, multihat_colorways=None) -> dict:
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
        _, current_digest = _stock_refs(self.settings, row.stage, row.color_key)
        if current_digest != row.stock_sha256:
            raise EcommerceError("ecommerce_stock_changed", "Official stock revision changed.")
        if row.stage in ("prod_s2_ec2", "prod_s2_ec3") and not row.front_reference_remote_file_id:
            raise EcommerceError("ecommerce_front_reference_missing", "Pinned Front reference is missing.")
        if row.last_error_code == "ecommerce_skill_changed":
            selected = _assigned_skill(self.session, self.settings, tenant_id, row.stage)
            row.skill_source = selected.source
            row.skill_id = selected.skill_id
            row.skill_name = selected.skill_name
            row.skill_version = selected.skill_version
            row.skill_bundle_sha256 = installed_stage2_skill_sha256(
                selected.skill_name, settings=self.settings)
        row.retry_count += 1
        row.status = "queued"
        row.last_error_code = row.last_error_message = None
        if row.stage in SCENE_GROUPS["prod_s2_ugc"] and scout_reference is None:
            previous = self.session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
            scout_reference = (previous.payload_json or {}).get("scout_reference") if previous else None
        if row.stage in SCENE_GROUPS["prod_s2_mh"]:
            previous = self.session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
            multihat_references = (
                validate_pinned_multihat_references(multihat_references)
                or validate_pinned_multihat_references(
                    (previous.payload_json or {}).get("multihat_references") if previous else None)
            )
            if not multihat_references:
                try:
                    multihat_references = select_multihat_references(
                        self.session, tenant_id=tenant_id,
                        source_plan_id=plan.id, source_revision=plan.source_revision,
                    )
                except MultiHatReferencesUnavailable as exc:
                    raise EcommerceError("multihat_references_missing", str(exc), 409) from exc
            # Automatic/manual retry MUST retain both color order and hashes.
            previous_payload = previous.payload_json or {} if previous else {}
            old_colors = validated_palette(row.stage, previous_payload.get("multihat_colorways"))
            if old_colors:
                old_hashes = previous_payload.get("multihat_colorway_hashes")
                try:
                    checked_palette_stock(self.settings, row.stage, old_colors, old_hashes)
                except MultiHatColorwayError as exc:
                    raise EcommerceError("multihat_stock_changed", str(exc), 409) from exc
                multihat_colorways, multihat_colorway_hashes = old_colors, old_hashes
            else:
                if multihat_colorways is None:
                    multihat_colorways = self._choose_multihat_palettes(
                        tenant_id=tenant_id, plan=plan, color_key=row.color_key)[row.stage]
                multihat_colorways, multihat_colorway_hashes = self._multihat_stock_snapshot(
                    row.stage, multihat_colorways)
        else:
            multihat_colorway_hashes = None
        self._enqueue(row, scout_reference=scout_reference, multihat_references=multihat_references,
                      multihat_colorways=multihat_colorways,
                      multihat_colorway_hashes=multihat_colorway_hashes)
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
        _, digest = _stock_refs(self.settings, row.stage, row.color_key)
        if digest != row.stock_sha256:
            raise EcommerceError("ecommerce_stock_changed", "Official stock reference changed.")
        if row.stage in ("prod_s2_ec2", "prod_s2_ec3"):
            front = _front_job(self.session, plan, row.color_key)
            row.front_reference_remote_file_id = front.output_remote_file_id
            row.front_reference_content_type = front.output_content_type
            row.front_reference_size_bytes = front.output_size_bytes
            row.front_preview_remote_file_id = front.front_preview_remote_file_id
            row.front_preview_content_type = front.front_preview_content_type
            row.front_preview_size_bytes = front.front_preview_size_bytes
            row.front_preview_design_sha256 = front.front_preview_design_sha256
        # Keep the version's original Skill pinned; do not silently switch to
        # another assigned Skill in a historical photo job.
        row.status = "queued"
        row.retry_count = 0
        row.started_at = None
        row.completed_at = None
        row.last_error_code = row.last_error_message = None
        multihat_references = None
        if row.stage in SCENE_GROUPS["prod_s2_mh"]:
            previous = self.session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
            multihat_references = validate_pinned_multihat_references(
                (previous.payload_json or {}).get("multihat_references") if previous else None)
            if not multihat_references:
                try:
                    multihat_references = select_multihat_references(
                        self.session, tenant_id=tenant_id,
                        source_plan_id=plan.id, source_revision=plan.source_revision,
                    )
                except MultiHatReferencesUnavailable as exc:
                    raise EcommerceError("multihat_references_missing", str(exc), 409) from exc
        multihat_colorways, multihat_colorway_hashes = None, None
        if row.stage in SCENE_GROUPS["prod_s2_mh"]:
            # Explicit Regenerate is a NEW generation; keep old output history
            # but randomize fresh colors. Exclude colors pinned on other shots.
            previous_palettes = self._saved_multihat_palettes(
                tenant_id=tenant_id, plan=plan, color_key=row.color_key)
            previous_colors = previous_palettes.pop(row.stage, [])
            available_colors = available_stock_colors(self.settings)
            try:
                fresh = choose_palettes(
                    tenant_id=tenant_id, source_plan_id=plan.id,
                    source_revision=plan.source_revision,
                    available=available_colors,
                    pinned=previous_palettes,
                )
            except MultiHatColorwayError as exc:
                raise EcommerceError("multihat_colorways_unavailable", str(exc), 409) from exc
            regenerated = different_regeneration_palette(
                row.stage, fresh[row.stage], previous_colors, available_colors)
            multihat_colorways, multihat_colorway_hashes = self._multihat_stock_snapshot(
                row.stage, regenerated)
        self._enqueue(row, multihat_references=multihat_references,
                      multihat_colorways=multihat_colorways,
                      multihat_colorway_hashes=multihat_colorway_hashes)
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
            "color_key": row.color_key,
            "status": _effective_status(row, processing), "retry_count": row.retry_count,
            "attempt_count": processing.attempt_count if processing else 0,
            "error_code": row.last_error_code or (processing.last_error_code if processing else None),
            "output_url": f"/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/{row.id}/output"
                          if row.output_remote_file_id else None,
            "skill_name": row.skill_name,
            "scene_reference": (processing.payload_json or {}).get("scout_reference")
                if processing and row.stage in SCENE_GROUPS["prod_s2_ugc"] else None,
            "scene_references": validate_pinned_multihat_references(
                (processing.payload_json or {}).get("multihat_references")
            ) if processing and row.stage in SCENE_GROUPS["prod_s2_mh"] else [],
            "scene_colorways": [
                {"key": key, "label": COLOR_NAMES[key]}
                for key in validated_palette(
                    row.stage, (processing.payload_json or {}).get("multihat_colorways"))
            ] if processing and row.stage in SCENE_GROUPS["prod_s2_mh"] else [],
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
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
        except FrontReferenceError:
            self._failure(context, "ecommerce_artwork_invalid",
                          "Cannot compose an embroidered front reference.", terminal=True)
            return JobHandlerResult.non_retryable(
                "ecommerce_artwork_invalid", "Invalid embroidery artwork.")
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

    def _dispatch_run_all_followups(self, context: JobHandlerContext,
                                   job_id: str, processing_job_id: str | None) -> None:
        """Complete the durable run-all handoff after Front output is committed.

        Child enqueue errors are isolated: a safely persisted Front output
        must never be marked failed just because a downstream Skill is missing.
        """
        if not processing_job_id:
            return
        with context.dependencies.session_factory() as session:
            processing = session.get(ProcessingJobModel, processing_job_id)
            if (not processing or processing.tenant_id != context.job.tenant_id
                    or processing.entity_id != job_id):
                return
            payload = processing.payload_json or {}
            stages = payload.get("run_all_followups", [])
            user_id = payload.get("run_all_user_id")
            if not isinstance(stages, list) or not isinstance(user_id, str) or not user_id:
                return
            front = session.get(RrugcEcommerceJobModel, job_id)
            if not front or front.status != "completed" or not front.output_remote_file_id:
                return
            source_plan_id = front.source_plan_id
            source_revision = front.source_revision
            color_key = front.color_key
        for stage in stages:
            if stage not in ("prod_s2_ec2", "prod_s2_ec3"):
                continue
            with context.dependencies.session_factory() as followup_session:
                try:
                    service = EcommerceService(followup_session, self.settings or get_settings())
                    existing = followup_session.scalar(select(RrugcEcommerceJobModel).where(
                        RrugcEcommerceJobModel.tenant_id == context.job.tenant_id,
                        RrugcEcommerceJobModel.source_plan_id == source_plan_id,
                        RrugcEcommerceJobModel.source_revision == source_revision,
                        RrugcEcommerceJobModel.stage == stage,
                        RrugcEcommerceJobModel.color_key == color_key))
                    processing = (followup_session.get(ProcessingJobModel, existing.processing_job_id)
                                  if existing and existing.processing_job_id else None)
                    if existing and _effective_status(existing, processing) == "failed":
                        service.retry(tenant_id=context.job.tenant_id, job_id=existing.id)
                    else:
                        service.queue(tenant_id=context.job.tenant_id, user_id=user_id,
                                      source_plan_id=source_plan_id, stage=stage, color_key=color_key)
                except EcommerceError as exc:
                    followup_session.rollback()
                    context.logger.warning("ecommerce_run_all_followup_blocked",
                                           extra={"stage": stage, "error_code": exc.code})
                except Exception:
                    followup_session.rollback()
                    context.logger.exception("ecommerce_run_all_followup_failed",
                                             extra={"stage": stage})

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
            refs, digest = _stock_refs(settings, row.stage, row.color_key)
            if digest != row.stock_sha256:
                raise EcommerceError("ecommerce_stock_changed", "Pinned stock content changed.")
            multihat_colors: list[str] = []
            multi_color_stock: list[tuple[str, bytes]] = []
            if row.stage in SCENE_GROUPS["prod_s2_mh"]:
                pinned_palette = context.job.payload.get("multihat_colorways")
                if pinned_palette is not None:
                    try:
                        multi_color_stock = checked_palette_stock(
                            settings, row.stage, pinned_palette,
                            context.job.payload.get("multihat_colorway_hashes"))
                    except MultiHatColorwayError as exc:
                        raise EcommerceError("multihat_stock_changed", str(exc), 409) from exc
                    multihat_colors = [key for key, _ in multi_color_stock]
            front_asset = None
            if row.stage in ("prod_s2_ec2", "prod_s2_ec3"):
                if not row.front_reference_remote_file_id:
                    raise EcommerceError("ecommerce_front_reference_missing", "Pinned Front input is missing.")
                front_asset = (row.front_reference_remote_file_id,
                               row.front_reference_content_type, row.front_reference_size_bytes)
            plan_id, mime, size, source_file_id = (
                plan.id, plan.source_mime_type, plan.source_size_bytes, plan.source_file_id)
            folder_id, stage, skill_name, color_key = plan.source_parent_folder_id, row.stage, row.skill_name, row.color_key
            preview_remote_id = row.front_preview_remote_file_id
            preview_mime = row.front_preview_content_type
            preview_size = row.front_preview_size_bytes
            preview_design_sha = row.front_preview_design_sha256
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
            design_sha = hashlib.sha256(design_bytes).hexdigest()
            if preview_remote_id:
                if preview_design_sha != design_sha:
                    raise EcommerceError("ecommerce_design_changed",
                                         "Embroidery artwork changed after preview was pinned.")
                preview_bytes = await self._open_bytes(
                    storage, context, "rrugc-ecommerce-front-preview:" + context.job.entity_id,
                    preview_remote_id, preview_mime, preview_size)
            elif stage == "prod_s2_ec1":
                preview_bytes = render_front_reference(refs[0][1], design_bytes)
                stored_preview = await storage.store_asset(StoreAssetInput(
                    tenant_id=context.job.tenant_id,
                    content_hash=hashlib.sha256(preview_bytes).hexdigest(),
                    body=_bytes_body(preview_bytes),
                    asset_id="rrugc-ecommerce-front-preview:" + context.job.entity_id,
                    content_type="image/png", size_bytes=len(preview_bytes),
                    filename=f"input_embroidered_front_{context.job.entity_id}.png",
                    destination_folder_id=folder_id or None,
                ))
                if not stored_preview.remote_file_id:
                    raise StorageProviderError("Front preview input could not be persisted.",
                                               retryable=True, code="ecommerce_preview_storage_failed")
                with context.dependencies.session_factory() as pin_session:
                    pinned = pin_session.scalar(select(RrugcEcommerceJobModel).where(
                        RrugcEcommerceJobModel.tenant_id == context.job.tenant_id,
                        RrugcEcommerceJobModel.id == context.job.entity_id,
                    ).with_for_update())
                    if pinned is None:
                        return JobHandlerResult.non_retryable(
                            "ecommerce_job_not_found", "Ecommerce job missing.")
                    pinned.front_preview_remote_file_id = stored_preview.remote_file_id
                    pinned.front_preview_content_type = "image/png"
                    pinned.front_preview_size_bytes = len(preview_bytes)
                    pinned.front_preview_design_sha256 = design_sha
                    pin_session.commit()
                preview_remote_id = stored_preview.remote_file_id
            else:
                preview_bytes = None
            if preview_bytes:
                references.append(ReferenceImageInput(
                    image=_prepared_image(preview_bytes), role="embroidered_front_preview",
                    label="persisted artwork fitted inside calibrated front embroidery area"))
        front_image = None
        if front_asset:
            front_remote, front_mime, front_size = front_asset
            front_image = _prepared_image(await self._open_bytes(
                storage, context, "rrugc-ecommerce-front:" + context.job.entity_id,
                front_remote, front_mime, front_size))
        multihat_loaded = 0
        if stage in SCENE_GROUPS["prod_s2_mh"]:
            pinned = validate_pinned_multihat_references(
                context.job.payload.get("multihat_references"))
            if not pinned:
                raise EcommerceError("multihat_references_missing",
                    "Slot 5 requires a pinned 3/4-hat Cap Stack selection.", 409)
            try:
                scene_sheets, multihat_loaded = await build_multihat_reference_sheets(pinned)
            except MultiHatReferencesUnavailable as exc:
                raise RrugcGenerationHandlerError(
                    "multihat_reference_download_failed", str(exc), retryable=True) from exc
            references.extend(scene_sheets)
            if multi_color_stock:
                # Authentic per-color stock photos override monochrome base
                # only for color; the base still defines factory geometry.
                references.append(make_palette_board(stage, multi_color_stock))
        if stage in SCENE_GROUPS["prod_s2_ugc"]:
            scout = context.job.payload.get("scout_reference")
            if not isinstance(scout, dict) or not allowed_scout_image_url(str(scout.get("image_url", ""))):
                raise EcommerceError("ugc_scout_reference_missing", "Pinned Scout image unavailable.")
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
                response = await client.get(scout["image_url"])
                response.raise_for_status()
                if len(response.content) > MAX_REFERENCE_BYTES:
                    raise EcommerceError("ugc_scout_reference_too_large", "Scout image exceeds limit.")
                if response.headers.get("content-type", "").split(";")[0] not in (
                    "image/jpeg", "image/png", "image/webp"):
                    raise EcommerceError("ugc_scout_reference_type", "Scout reference is not an image.")
                scene_image = _prepared_image(response.content)
            references.append(ReferenceImageInput(
                image=scene_image, role="scene_reference",
                label="pinned approved lifestyle reference; preserve composition only"))
        # Immutable official stock is addressed by its hash, not a transient browser preview.
        base = _prepared_image(refs[0][1])
        if front_image:
            references.append(ReferenceImageInput(
                image=front_image, role="front_reference", label="completed embroidered front photo"))
        if len(refs) > 1:
            references.append(ReferenceImageInput(
                image=_prepared_image(refs[1][1]), role="side_reference",
                label="official true right-side stock geometry"))
        if len(refs) > 2:
            references.append(ReferenceImageInput(
                image=_prepared_image(refs[2][1]), role="back_reference",
                label="official Valucap 8869 back panel stock geometry"))
        prompt = (
            ("Use the bundled Cap Stack contact sheets as composition references: "
             f"{multihat_loaded} independently scouted real 3/4-hat photographs "
             "from GatorHats Reviews Stage 1. Each small panel represents a separate "
             "scene, NOT a montage to reproduce. Create ONE original complete photo "
             "with the correct count of official 8869 caps; NEVER render a grid, "
             "contact sheet, source website watermark or collage. Prioritize the exact "
             "provided embroidery design and verified official stock over any "
             "different branding or hat construction in the Pinterest scene photos. "
             if stage in SCENE_GROUPS["prod_s2_mh"] else "")
            + (multihat_color_prompt(stage, multihat_colors) if multihat_colors else "")
            + ("Generate ONE photorealistic original scene with multiple DIFFERENT "
               "color Valucap 8869 five-panel caps. The supplied single-hat BASE stock "
               "only defines construction and crown geometry: do not reproduce "
               "its color on all caps. "
               if multihat_colors
               else ("Generate ONE independent photorealistic scene for this uploaded "
                     "Multi-hat or UGC Skill using the official Valucap 8869 "
                     + color_key.replace("-", "/") + " five-panel cap. "
                     if stage in SCENE_STAGE_SLOTS
                     else "Generate ONE photorealistic commercial ecommerce product photograph "
                          "of the official Valucap 8869 "
                          + color_key.replace("-", "/") + " five-panel twill cap. "))
            + VIEW_PROMPTS[stage]
            + " " + embroidery_prompt_for_product_slot(stage)
            + " Keep all cap panel seams, fabric, bill curvature, selected-color fabric "
            "colors and hardware strictly consistent with the official reference photos. "
            "The approved GatorHats side branding is allowed only on the visible side of the cap; no unrelated logos, added decorations, invented slogans or merged panels."
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
                                    "front_preview_remote_file_id": row.front_preview_remote_file_id,
                                    "front_preview_design_sha256": row.front_preview_design_sha256,
                                    "multihat_source_ids": [
                                        item["image_id"] for item in
                                        validate_pinned_multihat_references(
                                            context.job.payload.get("multihat_references"))
                                    ] if stage in SCENE_GROUPS["prod_s2_mh"] else [],
                                    "multihat_loaded": multihat_loaded,
                                    "multihat_colorways": multihat_colors,
                                    "multihat_colorway_hashes": (
                                        context.job.payload.get("multihat_colorway_hashes")
                                        if multihat_colors else None),
                                }})
            row.output_remote_file_id = stored.remote_file_id
            row.output_content_type = generated.mime_type
            row.output_size_bytes = len(generated.image_bytes)
            row.output_width, row.output_height = width, height
            row.completed_at = datetime.now(timezone.utc)
            row.status = "completed"
            row.last_error_code = row.last_error_message = None
            session.commit()
            completed_stage = row.stage
            completed_job_id = row.id
            completed_processing_id = row.processing_job_id
            completed_source_plan_id = row.source_plan_id
            completed_source_revision = row.source_revision
            completed_color_key = row.color_key
        if completed_stage == "prod_s2_ec1":
            try:
                self._dispatch_run_all_followups(
                    context, completed_job_id, completed_processing_id)
            except Exception:
                # Front is already safely saved. Never request a duplicate
                # provider generation because follow-up scheduling failed.
                context.logger.exception("ecommerce_run_all_followup_dispatch_failed",
                                         extra={"job_id": completed_job_id})
        try:
            from app.modules.realistic_review_ugc.products_run_all import advance_after_output
            advance_after_output(
                context.dependencies.session_factory,
                tenant_id=context.job.tenant_id, source_plan_id=completed_source_plan_id,
                source_revision=completed_source_revision, color_key=completed_color_key,
            )
        except Exception:
            # Recovery scheduler retries the handoff. Already-saved output
            # must never be regenerated to compensate for scheduling failure.
            context.logger.exception("products_run_all_ecommerce_handoff_failed",
                                     extra={"job_id": completed_job_id})
        return JobHandlerResult.completed()