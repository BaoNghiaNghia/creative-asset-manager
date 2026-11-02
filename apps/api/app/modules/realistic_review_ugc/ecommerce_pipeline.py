"""Independent, pinned Ecommerce single-image jobs for four GatorHats 8869 views."""
from __future__ import annotations

import asyncio
import hashlib
import httpx
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import OpenStoredAssetInput, StoreAssetInput, StorageProviderError
from app.modules.image_generation.providers import PreparedImage, ReferenceImageInput, GeneratedImageResult
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.codex_account_manager import CodexAccountPoolBusy, acquire_worker_profile
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.ecommerce_input_contract import SHOTS_BY_STAGE
from app.modules.realistic_review_ugc.ecommerce_stock_evidence import read_release_stock
from app.modules.realistic_review_ugc.hat_stock_sync import COLORS
from app.modules.realistic_review_ugc.ecommerce_front_reference import FrontReferenceError, render_front_reference
from app.modules.realistic_review_ugc.front_placement import (
    front_placement_prompt, job_front_placement, pin_front_placement,
)
from app.modules.realistic_review_ugc.embroidery_realism import embroidery_prompt_for_product_slot
from app.modules.realistic_review_ugc.slot1_embroidery import (
    Slot1EmbroideryQAError, build_slot1_material_pass, finish_slot1_refinement,
    lite_slot1_qa_error_code,
)
from app.modules.realistic_review_ugc.stage2_stock_inpaint import native_thread_macro
from app.modules.realistic_review_ugc.threadtransfer import composite_reference_on_cap
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
from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError,
    SLOT1_DIRECT_NAME, SLOT1_DIRECT_VERSION, SLOT1_DIRECT_SHA256,
    SLOT1_DIRECT_PREVIOUS_VERSION, SLOT1_DIRECT_PREVIOUS_SHA256,
    SLOT1_DIRECT_CHECKPOINTED_VERSION, SLOT1_DIRECT_CHECKPOINTED_SHA256,
    SLOT1_LEGACY_DIRECT_NAME, SLOT1_LEGACY_DIRECT_VERSION,
    SLOT1_LEGACY_DIRECT_SHA256,
)

JOB_TYPE = "rrugc_ecommerce_generate"
# Reserve an interactive lane above bulk index backlog, without bypassing policies.
ECOMMERCE_INTERACTIVE_PRIORITY = 96
MAX_MANUAL_RETRIES = 3


def _slot1_direct_selection():
    """Built-in native embroidery edits for new Ecommerce Slots 1–3 and stock-only 4."""
    from app.modules.realistic_review_ugc.stage2_skills import ResolvedStage2Skill
    return ResolvedStage2Skill(
        source="internal", skill_id=None,
        skill_name=SLOT1_DIRECT_NAME, skill_version=SLOT1_DIRECT_VERSION,
    )


def _skill_snapshot_sha(skill, settings):
    return (SLOT1_DIRECT_SHA256 if skill.skill_name == SLOT1_DIRECT_NAME
            and skill.source == "internal"
            else installed_stage2_skill_sha256(skill.skill_name, settings=settings))


_lite_slot1_qa_error_code = lite_slot1_qa_error_code


# All NEW EC2 jobs use the actual selected-color 3/4 photo as the edit target.
# Legacy jobs keep their pinned front+side evidence until an EXPLICIT Retry/Regenerate.
LEGACY_EC2_STOCK_ROLES = ("stock_front", "stock_side_90")
STOCK_ROLES = {
    "prod_s2_ec1": ("stock_front",),
    "prod_s2_ec2": ("stock_three_quarter",),
    "prod_s2_ec3": ("stock_side_90",),
    "prod_s2_ec4": ("stock_back",),
}

VIEW_PROMPTS = {
    "prod_s2_ec1": (
        "FRONT high straight-on catalog photo. Apply exactly the original embroidery "
        "inside the cap's approved front embroidery area, matching the Blueprint reference "
        "frame on the original 4:5 hat photograph, using the pinned calibration "
        "coordinates in the job instructions. Preserve the artwork aspect ratio without distortion, fitting "
        "within this full guide rectangle. Preserve exact lettering, thread colors, "
        "stitch structure and the five-panel crown. No design outside the guide area. Preserve the exact GatorHats side wordmark on the cap side if it is naturally visible."
    ),
    "prod_s2_ec2": (
        "SLOT 2 / EXACT FACTORY THREE-QUARTER STOCK EDIT. The provided Edit target "
        "is an existing REAL FRONT-RIGHT 3/4 Valucap 8869 photo of the SELECTED color. "
        "Do not turn, rotate, invent, reshoot, generate or reconstruct the hat or angle. "
        "Preserve its original camera angle, frame, background, five-panel seams, brim, "
        "bill stitching, textile weave, lighting, shadow, hardware and factory side branding. "
        "EDIT ONLY the visible front-crown embroidery surface to add fine genuine machine "
        "stitches. Transfer the EXACT source artwork's lettering, silhouette and thread "
        "colors, using completed embroidered-front photo ONLY for identity and relative "
        "placement; foreshorten the design against the REAL angled panel. "
        "The rest of the source photo must remain visually unchanged. "
        "No new text, altered cap, puffy embroidery, printed decal or fake 3D relief."
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

LEGACY_EC2_VIEW_PROMPT = (
    "FRONT RIGHT THREE-QUARTER high catalog photo. Rotate the exact Valucap 8869 "
    "five-panel cap from the official frontal view toward the right. Use the separate "
    "right-side stock photo ONLY as evidence of its side geometry, not as a 3/4 photo. "
    "Preserve the already embroidered front-reference design, scaling and stitch colors "
    "with correct perspective on the FRONT crown. The approved product has small red GatorHats embroidered side branding near the lower side seam; show it naturally if visible. Do not invent other text."
)


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
# The processing rows use per-shot stage identifiers (e.g. prod_s2_mh_1).
# Parent slot names are never job.stage values; all visible-front scenes
# require the same pinned deterministic embroidery reference as Ecommerce 1.
FRONT_PREVIEW_STAGES = frozenset({"prod_s2_ec1", *SCENE_STAGE_SLOTS})
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


def _stock_refs(settings: Settings, stage: str, color_key: str = "natural-black",
                *, legacy_three_quarter: bool = False) -> tuple[list[tuple[str, bytes]], str]:
    if color_key not in COLORS:
        raise EcommerceError("ecommerce_color_invalid", "Unknown hat color.", 422)
    if stage not in STOCK_ROLES:
        raise EcommerceError("ecommerce_stage_invalid", "Unknown Ecommerce photo slot.", 422)
    # The checkout root is also available in the immutable production release.
    project_root = Path(__file__).resolve().parents[5]
    from app.modules.realistic_review_ugc.hat_stock_sync import stock_read_lock
    try:
        with stock_read_lock(settings):
            roles = (LEGACY_EC2_STOCK_ROLES
                     if stage == "prod_s2_ec2" and legacy_three_quarter
                     else STOCK_ROLES[stage])
            refs = [(role, read_release_stock(role, project_root=project_root, color_key=color_key)[0])
                    for role in roles]
    except (OSError, ValueError) as exc:
        raise EcommerceError("ecommerce_stock_unavailable",
                             "A verified GatorHats stock photo is unavailable.", 503) from exc
    digest = hashlib.sha256(b"".join(
        role.encode("utf-8") + b":" + hashlib.sha256(content).digest()
        for role, content in refs
    )).hexdigest()
    return refs, digest


def _pinned_stock_refs(settings: Settings, stage: str, color_key: str,
                       expected_sha256: str) -> tuple[list[tuple[str, bytes]], bool]:
    """Resolve immutable old/new EC2 stock snapshots without changing queued jobs."""
    try:
        refs, digest = _stock_refs(settings, stage, color_key)
        if digest == expected_sha256:
            return refs, False
    except EcommerceError:
        if stage != "prod_s2_ec2":
            raise
    if stage == "prod_s2_ec2":
        old_refs, old_digest = _stock_refs(settings, stage, color_key, legacy_three_quarter=True)
        if old_digest == expected_sha256:
            return old_refs, True
    raise EcommerceError("ecommerce_stock_changed", "Pinned stock content changed.")


def _rebind_ec2_stock_for_explicit_action(settings: Settings, row: RrugcEcommerceJobModel) -> None:
    """Only a user-initiated Retry/Regenerate may upgrade an old EC2 stock pin."""
    _, current_sha = _stock_refs(settings, row.stage, row.color_key)
    if current_sha == row.stock_sha256:
        return
    if row.stage == "prod_s2_ec2":
        _, old_sha = _stock_refs(settings, row.stage, row.color_key, legacy_three_quarter=True)
        if old_sha == row.stock_sha256:
            row.stock_sha256 = current_sha
            return
    raise EcommerceError("ecommerce_stock_changed", "Official stock reference changed.")


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


def _repin_slot1_direct(row: RrugcEcommerceJobModel) -> bool:
    """Explicit Slot 1 Retry/Regenerate selects the built-in edit, never a ZIP.

    Running/queued jobs keep their pinned previous provider and contract.
    Historical output versions are immutable.
    """
    if row.stage != "prod_s2_ec1":
        return False
    selected = _slot1_direct_selection()
    if row.skill_name != SLOT1_DIRECT_NAME:
        # An older flat input overlay is NOT a real photographic Pass A.
        row.front_preview_remote_file_id = None
        row.front_preview_content_type = None
        row.front_preview_size_bytes = None
        row.front_preview_design_sha256 = None
    row.skill_source = selected.source
    row.skill_id = selected.skill_id
    row.skill_name = selected.skill_name
    row.skill_version = selected.skill_version
    row.skill_bundle_sha256 = SLOT1_DIRECT_SHA256
    return True


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
                             "Complete Stage 2 Slot 1 before applying embroidery to another slot.")
    return front


def _front_material_job(session: Session, plan: RrugcSourcePlanModel,
                        color_key: str = "natural-black") -> RrugcEcommerceJobModel:
    """Only a completed V2 front with a durable native Pass A macro may seed downstream edits."""
    front = _front_job(session, plan, color_key)
    if front.skill_name != SLOT1_DIRECT_NAME or not front.front_preview_remote_file_id:
        raise EcommerceError("ecommerce_thread_reference_missing",
                             "Regenerate Slot 1 to save the photographic thread reference before running other slots.")
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
                 multihat_colorways=None, multihat_colorway_hashes=None,
                 front_placement=None, regenerate_material_reference: bool = False) -> None:
        count = int(self.session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == row.tenant_id,
            ProcessingJobModel.entity_type == "rrugc_ecommerce_job",
            ProcessingJobModel.entity_id == row.id,
        )) or 0)
        # A saved reference makes V2.4 Retry Placement a CPU-only compositor.
        # Do not subject it to Codex provider quota/policy admission controls.
        local_transfer = bool(
            row.stage == "prod_s2_ec1"
            and row.skill_name == SLOT1_DIRECT_NAME
            and row.skill_version == SLOT1_DIRECT_VERSION
            and row.front_preview_remote_file_id
            and row.front_preview_design_sha256
            and not regenerate_material_reference
        )
        processing = ProcessingRepository(self.session, self.settings).create_job(
            tenant_id=row.tenant_id, job_type=JOB_TYPE, entity_type="rrugc_ecommerce_job",
            entity_id=row.id, idempotency_key=f"rrugc-ecommerce:{row.id}:{count}",
            payload={
                "ecommerce_job_id": row.id,
                **({"regenerate_material_reference": True}
                   if regenerate_material_reference and row.stage == "prod_s2_ec1" else {}),
                **({"thread_material_pipeline": "v1"} if (
                    row.stage in {"prod_s2_ec2", "prod_s2_ec3"} and row.skill_source == "internal"
                    or row.stage in SCENE_STAGE_SLOTS and row.front_reference_remote_file_id
                    and row.front_preview_remote_file_id
                ) else {}),
                **({"front_placement": front_placement} if front_placement is not None else {}),
                **({"scout_reference": scout_reference} if scout_reference else {}),
                **({"multihat_references": multihat_references} if multihat_references else {}),
                **({"multihat_colorways": multihat_colorways,
                    "multihat_colorway_hashes": multihat_colorway_hashes}
                   if multihat_colorways else {}),
            }, priority=20, max_attempts=3,
            provider_key=None if local_transfer else "codex",
            provider_scope=None if local_transfer else "ai",
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
        skill = (_slot1_direct_selection() if stage in SHOTS_BY_STAGE
                 else _assigned_skill(self.session, self.settings, tenant_id, stage))
        front = (_front_material_job(self.session, plan, color_key)
                 if stage in {"prod_s2_ec2", "prod_s2_ec3", *SCENE_STAGE_SLOTS}
                 else None)
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
            skill_bundle_sha256=_skill_snapshot_sha(skill, self.settings),
            created_by_user_id=user_id, status="queued",
        )
        self.session.add(row)
        self.session.flush()
        self._enqueue(row, scout_reference=scout_reference, multihat_references=multihat_references,
                      multihat_colorways=multihat_colorways,
                      multihat_colorway_hashes=multihat_colorway_hashes,
                      front_placement=pin_front_placement(self.session, tenant_id, plan.source_revision))
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
                from app.modules.realistic_review_ugc.ugc_verified_reuse import (
                    load_products_ugc_references,
                )
                candidates = load_products_ugc_references(
                    self.session, tenant_id=tenant_id, campaign_id=plan.campaign_id,
                )
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
                    # Only require official stock while Slot 1 is running;
                    # the built-in native ImageGen pipeline needs no ZIP Skill.
                    _stock_refs(self.settings, stage, color_key)
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
        _rebind_ec2_stock_for_explicit_action(self.settings, row)
        if row.stage in ("prod_s2_ec2", "prod_s2_ec3") and not row.front_reference_remote_file_id:
            raise EcommerceError("ecommerce_front_reference_missing", "Pinned Front reference is missing.")
        if row.stage in SHOTS_BY_STAGE:
            if row.stage == "prod_s2_ec1":
                _repin_slot1_direct(row)
            else:
                selected = _slot1_direct_selection()
                row.skill_source, row.skill_id = selected.source, None
                row.skill_name, row.skill_version = selected.skill_name, selected.skill_version
                row.skill_bundle_sha256 = SLOT1_DIRECT_SHA256
                if row.stage in {"prod_s2_ec2", "prod_s2_ec3"}:
                    front = _front_material_job(self.session, plan, row.color_key)
                    row.front_reference_remote_file_id = front.output_remote_file_id
                    row.front_reference_content_type = front.output_content_type
                    row.front_reference_size_bytes = front.output_size_bytes
                    row.front_preview_remote_file_id = front.front_preview_remote_file_id
                    row.front_preview_content_type = front.front_preview_content_type
                    row.front_preview_size_bytes = front.front_preview_size_bytes
                    row.front_preview_design_sha256 = front.front_preview_design_sha256
        elif row.last_error_code == "ecommerce_skill_changed":
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
        if (row.stage in SCENE_STAGE_SLOTS and processing is not None
                and (processing.payload_json or {}).get("thread_material_pipeline") == "v1"):
            # Retry old pinned scene jobs without changing their legacy inputs.
            # New scenes keep the native V2 reference, verified again.
            front = _front_material_job(self.session, plan, row.color_key)
            row.front_reference_remote_file_id = front.output_remote_file_id
            row.front_reference_content_type = front.output_content_type
            row.front_reference_size_bytes = front.output_size_bytes
            row.front_preview_remote_file_id = front.front_preview_remote_file_id
            row.front_preview_content_type = front.front_preview_content_type
            row.front_preview_size_bytes = front.front_preview_size_bytes
            row.front_preview_design_sha256 = front.front_preview_design_sha256
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
        previous_processing = self.session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
        previous_placement = (previous_processing.payload_json or {}).get("front_placement") if previous_processing else None
        self._enqueue(row, scout_reference=scout_reference, multihat_references=multihat_references,
                      multihat_colorways=multihat_colorways,
                      multihat_colorway_hashes=multihat_colorway_hashes,
                      front_placement=previous_placement)
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
        _rebind_ec2_stock_for_explicit_action(self.settings, row)
        if row.stage in ("prod_s2_ec2", "prod_s2_ec3") or row.stage in SCENE_STAGE_SLOTS:
            front = _front_material_job(self.session, plan, row.color_key)
            row.front_reference_remote_file_id = front.output_remote_file_id
            row.front_reference_content_type = front.output_content_type
            row.front_reference_size_bytes = front.output_size_bytes
            row.front_preview_remote_file_id = front.front_preview_remote_file_id
            row.front_preview_content_type = front.front_preview_content_type
            row.front_preview_size_bytes = front.front_preview_size_bytes
            row.front_preview_design_sha256 = front.front_preview_design_sha256
        if row.stage in SHOTS_BY_STAGE:
            selected = _slot1_direct_selection()
            row.skill_source, row.skill_id = selected.source, None
            row.skill_name, row.skill_version = selected.skill_name, selected.skill_version
            row.skill_bundle_sha256 = SLOT1_DIRECT_SHA256
        # Keep the prior Slot 1 reference visible while a new Pass A is being
        # made. The verified checkpoint replaces it ONLY after Drive storage;
        # previous front/reference versions remain immutable.
        # Keep the original pinned Skill for all other slots. An explicit
        # Slot 1 regeneration alone can intentionally adopt True Thread v2;
        # the previous output version and its original Skill SHA remain saved.
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
                      multihat_colorway_hashes=multihat_colorway_hashes,
                      front_placement=pin_front_placement(self.session, tenant_id, plan.source_revision),
                      regenerate_material_reference=row.stage == "prod_s2_ec1")
        self.session.commit()
        return {"job_id": row.id, "status": row.status}

    def retry_inpaint(self, *, tenant_id: str, job_id: str) -> dict:
        """Retry ThreadTransfer placement using the pinned Embroidery Reference.

        Historical versions are immutable. Newly queued V2.4 jobs composite
        locally without any ImageGen or Codex lease; older in-flight jobs keep
        their pinned contract. This never regenerates reference material.
        """
        row = self.session.scalar(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.tenant_id == tenant_id,
            RrugcEcommerceJobModel.id == job_id,
            RrugcEcommerceJobModel.stage == "prod_s2_ec1",
        ).with_for_update())
        if row is None:
            raise EcommerceError("ecommerce_job_not_found", "Front Ecommerce job not found.", 404)
        current = (self.session.get(ProcessingJobModel, row.processing_job_id)
                   if row.processing_job_id else None)
        if _effective_status(row, current) not in ("completed", "failed"):
            raise EcommerceError("ecommerce_inpaint_retry_unavailable",
                                 "Wait until the current front edit completes.")
        plan = _source_plan(self.session, tenant_id, row.source_plan_id, lock=True)
        if plan.source_revision != row.source_revision:
            raise EcommerceError("ecommerce_source_changed", "Embroidery artwork revision changed.")
        if (row.skill_name != SLOT1_DIRECT_NAME
                or not row.front_preview_remote_file_id
                or row.front_preview_content_type != "image/png"
                or not row.front_preview_design_sha256):
            raise EcommerceError("ecommerce_thread_reference_missing",
                                 "Generate the Embroidery Reference before retrying inpaint.")
        _repin_slot1_direct(row)
        row.status = "queued"
        row.retry_count = 0 if current and current.status == "completed" else row.retry_count + 1
        row.started_at = row.completed_at = None
        row.last_error_code = row.last_error_message = None
        # Retry Placement uses the latest approved geometry / design-size
        # calibration. The historical completed version keeps its old snapshot.
        placement = pin_front_placement(self.session, tenant_id, plan.source_revision)
        self._enqueue(row, front_placement=placement)
        self.session.commit()
        return {"job_id": row.id, "status": row.status,
                "embroidery_reference_reused": True}

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
            "embroidery_reference_url": (
                f"/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/{row.id}/output?kind=embroidery_reference"
                if (row.stage == "prod_s2_ec1" and row.skill_name == SLOT1_DIRECT_NAME
                    and row.front_preview_remote_file_id
                    and row.front_preview_content_type == "image/png"
                    and row.front_preview_design_sha256) else None
            ),
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

    async def _checkpointed_slot1_generate(
        self, *, context: JobHandlerContext, storage, runner: CodexImageGenRunner | None,
        person: PreparedImage, references: list[ReferenceImageInput], prompt: str,
        source_revision: str, design_sha: str, folder_id: str | None,
        preview_remote_id: str | None, preview_mime: str | None,
        preview_size: int | None, preview_design_sha: str | None,
        transfer_material=None, transfer_design_size: int = 100,
    ) -> tuple[GeneratedImageResult, str, int]:
        """Checkpoint native Embroidery Reference, then run local V2.4 transfer.

        Pinned V2.3 jobs retain their historical native Pass B contract.
        """
        material_bytes: bytes | None = None
        material_remote_id = preview_remote_id
        force_new = context.job.payload.get("regenerate_material_reference") is True
        if (transfer_material is not None and material_remote_id
                and not force_new and preview_design_sha != design_sha):
            # Never silently regenerate a reference on a pure placement Retry.
            raise EcommerceError("ecommerce_design_changed",
                                 "Pinned Embroidery Reference is for another design revision.")
        if material_remote_id and not force_new and preview_design_sha == design_sha:
            material_bytes = await self._open_bytes(
                storage, context, "rrugc-ecommerce-thread-material:" + context.job.entity_id,
                material_remote_id, preview_mime, preview_size,
            )
        if material_bytes is None:
            if runner is None:
                raise EcommerceError("ecommerce_thread_reference_missing",
                                     "Saved Embroidery Reference is unavailable.")
            material_runner = CodexImageGenRunner(replace(
                runner.config, output_contract="slot1_v2_material_single",
            ))
            try:
                material = await material_runner.generate_from_references(
                    attempt_id=context.job.entity_id + "-thread-reference",
                    person=next(ref.image for ref in references if ref.role == "design_reference"),
                    references=[], prompt=prompt,
                )
            finally:
                material_runner.cleanup_attempt(context.job.entity_id + "-thread-reference")
            material_bytes = material.image_bytes
            material_sha = hashlib.sha256(material_bytes).hexdigest()
            if (material.provider_metadata.get("native_sha256") != material_sha
                    or material_sha == design_sha):
                raise EcommerceError("ecommerce_thread_material_unverified",
                                     "Embroidery Reference must be a distinct verified native ImageGen output.")
        material_hash = hashlib.sha256(material_bytes).hexdigest()
        if len(material_bytes) > MAX_REFERENCE_BYTES:
            raise EcommerceError("ecommerce_thread_material_invalid", "Embroidery Reference exceeds the asset limit.")
        try:
            with Image.open(BytesIO(material_bytes)) as macro:
                if (macro.format != "PNG" or min(macro.size) < 512
                        or macro.width * macro.height > MAX_REFERENCE_PIXELS):
                    raise ValueError("Invalid macro image geometry.")
                macro.verify()
            with Image.open(BytesIO(material_bytes)) as macro:
                macro_width, macro_height = macro.size
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise EcommerceError("ecommerce_thread_material_invalid",
                                 "Embroidery Reference is not a verified PNG image.") from exc

        if material_remote_id is None or force_new or preview_design_sha != design_sha:
            stored = await storage.store_asset(StoreAssetInput(
                tenant_id=context.job.tenant_id,
                content_hash=material_hash, body=_bytes_body(material_bytes),
                asset_id="rrugc-ecommerce-thread-material:" + context.job.entity_id,
                content_type="image/png", size_bytes=len(material_bytes),
                filename="embroidery_reference_" + context.job.entity_id
                         + "_" + material_hash[:12] + ".png",
                destination_folder_id=folder_id or None,
            ))
            if not stored.remote_file_id:
                raise StorageProviderError("Embroidery Reference was not stored.",
                                           retryable=True, code="ecommerce_thread_material_storage_failed")
            material_remote_id = stored.remote_file_id
            with context.dependencies.session_factory() as session:
                pinned = session.scalar(select(RrugcEcommerceJobModel).where(
                    RrugcEcommerceJobModel.tenant_id == context.job.tenant_id,
                    RrugcEcommerceJobModel.id == context.job.entity_id,
                ).with_for_update())
                if pinned is None or pinned.source_revision != source_revision:
                    raise EcommerceError("ecommerce_source_changed",
                                         "Cannot checkpoint a reference for a different design.")
                pinned.front_preview_remote_file_id = material_remote_id
                pinned.front_preview_content_type = "image/png"
                pinned.front_preview_size_bytes = len(material_bytes)
                pinned.front_preview_design_sha256 = design_sha
                session.commit()

        if transfer_material is not None:
            # ThreadTransfer V1: reuse native photographic fibers as a raster
            # layer; NO second ImageGen call, no changed factory cap pixels.
            try:
                transferred = composite_reference_on_cap(
                    transfer_material, material_bytes, design_size=transfer_design_size,
                )
            except Slot1EmbroideryQAError as exc:
                # A failed layer extraction cannot be fixed by repeating the
                # same native call; keep the already-pinned macro for review.
                raise RrugcGenerationHandlerError(
                    _lite_slot1_qa_error_code(exc), str(exc), retryable=False,
                ) from exc
            photo_sha = hashlib.sha256(transferred.output_png).hexdigest()
            reused = not (force_new or preview_remote_id is None
                          or preview_design_sha != design_sha)
            return GeneratedImageResult(
                provider="codex", model=None,
                image_bytes=transferred.output_png, mime_type="image/png",
                provider_metadata={
                    "skill": SLOT1_DIRECT_NAME, "native_passes": 0 if reused else 1,
                    "reference_sha256": material_hash, "edited_sha256": photo_sha,
                    "reference_asset_id": material_remote_id,
                    "reference_reused": reused,
                    "edit_target": "original_factory_crown_raster",
                    "transfer_engine": "threadtransfer-v1",
                    "thread_layer_sha256": transferred.thread_layer_sha256,
                    "extracted_thread_pixels": transferred.extracted_pixels,
                    "transfer_box": list(transferred.target_box),
                },
            ), material_remote_id, len(material_bytes)

        material_ref = ReferenceImageInput(
            image=PreparedImage(image_bytes=material_bytes, mime_type="image/png",
                                width=macro_width, height=macro_height),
            role="slot1_thread_material_reference",
            label="Exact native Embroidery Reference saved as independent Pass A output; "
                  "copy fine yarn strand material ONLY, not its background or letter position.",
        )
        cap_runner = CodexImageGenRunner(replace(
            runner.config, output_contract="slot1_v2_cap_single",
        ))
        try:
            photo = await cap_runner.generate_from_references(
                attempt_id=context.job.entity_id + "-stock-inpaint", person=person,
                references=[*references, material_ref],
                prompt=prompt + " REUSE the supplied, already-saved Embroidery Reference. "
                       "No second embroidery-material generation. Sew threads close to "
                       "the curved real front crown; avoid oversized or puffy yarn ridges.",
            )
        finally:
            cap_runner.cleanup_attempt(context.job.entity_id + "-stock-inpaint")
        photo_sha = hashlib.sha256(photo.image_bytes).hexdigest()
        if (photo.provider_metadata.get("native_sha256") != photo_sha
                or photo_sha == material_hash):
            raise EcommerceError("ecommerce_front_inpaint_unverified",
                                 "Final cap must be a distinct verified native ImageGen edit.")
        return GeneratedImageResult(
            provider="codex", model=photo.model, image_bytes=photo.image_bytes,
            mime_type="image/png", provider_request_id=photo.provider_request_id,
            provider_metadata={
                "skill": SLOT1_DIRECT_NAME, "native_passes": 2,
                "reference_sha256": material_hash, "edited_sha256": photo_sha,
                "reference_asset_id": material_remote_id,
                "reference_reused": not (force_new or preview_remote_id is None
                                        or preview_design_sha != design_sha),
                "edit_target": "unaltered_factory_crown",
            },
        ), material_remote_id, len(material_bytes)

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
            is_direct_job = (
                row.stage in SHOTS_BY_STAGE
                and row.skill_source == "internal"
                and row.skill_name in {SLOT1_DIRECT_NAME, SLOT1_LEGACY_DIRECT_NAME}
            )
            if is_direct_job:
                # Running and previously queued v1 jobs retain their exact
                # pinned contract; explicit Retry/Regenerate adopts v2.
                expected = (
                    {(SLOT1_DIRECT_VERSION, SLOT1_DIRECT_SHA256),
                     (SLOT1_DIRECT_PREVIOUS_VERSION, SLOT1_DIRECT_PREVIOUS_SHA256),
                     (SLOT1_DIRECT_CHECKPOINTED_VERSION, SLOT1_DIRECT_CHECKPOINTED_SHA256)}
                    if row.skill_name == SLOT1_DIRECT_NAME
                    else {(SLOT1_LEGACY_DIRECT_VERSION, SLOT1_LEGACY_DIRECT_SHA256)}
                )
                if (row.skill_version, row.skill_bundle_sha256) not in expected:
                    raise EcommerceError("ecommerce_pipeline_changed", "Slot 1 built-in version changed.")
            else:
                # Preserve old queued two-pass and legacy jobs with their
                # historical pinned Skill; never switch one mid-flight.
                verify_stage2_skill_runtime(settings=settings, skill_source=row.skill_source,
                                            skill_id=row.skill_id, skill_name=row.skill_name,
                                            skill_version=row.skill_version)
                if row.skill_bundle_sha256 != installed_stage2_skill_sha256(row.skill_name, settings=settings):
                    raise EcommerceError("ecommerce_skill_changed", "Selected Skill revision changed.")
            refs, legacy_three_quarter = _pinned_stock_refs(
                settings, row.stage, row.color_key, row.stock_sha256)
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
            if row.stage in ("prod_s2_ec2", "prod_s2_ec3") or (
                row.stage in SCENE_STAGE_SLOTS and
                (context.job.payload.get("thread_material_pipeline") == "v1")
            ):
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
            pinned_reference_version = row.skill_version
            row.status = "running"
            row.started_at = row.started_at or datetime.now(timezone.utc)
            session.commit()

        # All new photo-edit slots reuse the verified native Slot 1 reference.
        # The rear stock view is already a correct, unembroidered product photo.
        slot1_material = None
        is_lite = stage == "prod_s2_ec1" and skill_name == "gatorhats-8869-slot1-threadtransfer-lite-v1"
        is_reference = stage == "prod_s2_ec1" and skill_name == SLOT1_DIRECT_NAME
        checkpointed_reference = is_reference and pinned_reference_version in {
            SLOT1_DIRECT_VERSION, SLOT1_DIRECT_CHECKPOINTED_VERSION,
        }
        is_threadtransfer = is_reference and pinned_reference_version == SLOT1_DIRECT_VERSION
        is_direct_legacy = stage == "prod_s2_ec1" and skill_name == SLOT1_LEGACY_DIRECT_NAME
        is_direct = is_reference or is_direct_legacy
        material_transfer = context.job.payload.get("thread_material_pipeline") == "v1"
        builtin_stock = stage in ("prod_s2_ec2", "prod_s2_ec3") and material_transfer and skill_name == SLOT1_DIRECT_NAME
        builtin_back = stage == "prod_s2_ec4" and skill_name == SLOT1_DIRECT_NAME
        references = []
        if stage != "prod_s2_ec4":
            design_bytes = await self._open_bytes(storage, context,
                    "rrugc-ecommerce-design:" + plan_id, source_file_id, mime, size)
            references.append(ReferenceImageInput(
                image=_prepared_image(design_bytes), role="design_reference",
                label="canonical embroidery design: preserve exact typography, silhouette, layout and colors; no fabric patch"))
            design_sha = hashlib.sha256(design_bytes).hexdigest()
            if is_reference:
                # V2 edits the original stock with its own material reference.
                # Do not render/upload a second flat-painted full-hat preview:
                # it is not an ImageGen input and can mislead downstream slots.
                # Slots 2/3 already receive the completed photographic front.
                preview_bytes = None
            elif preview_remote_id:
                if preview_design_sha != design_sha:
                    raise EcommerceError("ecommerce_design_changed",
                                         "Embroidery artwork changed after preview was pinned.")
                preview_bytes = await self._open_bytes(
                    storage, context, "rrugc-ecommerce-front-preview:" + context.job.entity_id,
                    preview_remote_id, preview_mime, preview_size)
            elif stage in FRONT_PREVIEW_STAGES and not material_transfer:
                front_placement = job_front_placement(context.job.payload.get("front_placement"))
                preview_bytes = render_front_reference(
                    refs[0][1], design_bytes,
                    guide=tuple(front_placement[key] for key in ("left", "top", "width", "height")),
                    design_size=front_placement.get("design_size", 100),
                )
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
            if stage == "prod_s2_ec1":
                # The old flat preview remains pinned for existing downstream
                # slots, but NEVER enters Slot 1 imagegen as a material example.
                slot1_material = build_slot1_material_pass(
                    refs[0][1], design_bytes,
                    job_front_placement(context.job.payload.get("front_placement")),
                    contact_width_px=(3 if is_lite or is_direct else 2 if skill_name == "gatorhats-8869-ecommerce-front-thread-v4" else 0),
                    localized_edit=is_lite or is_direct,
                    clean_soft_shadow=is_lite or is_direct,
                )
                edit_mask_bytes = slot1_material.edit_mask_png()
                edit_canvas_width, edit_canvas_height = slot1_material.edit_canvas_size
                if not is_direct:
                    references.append(ReferenceImageInput(
                        image=_prepared_image(refs[0][1]), role="unaltered_stock_reference",
                        label="exact fabric, lighting and factory logo; never repaint outside edit mask",
                    ))
                references.append(ReferenceImageInput(
                        image=PreparedImage(
                            image_bytes=edit_mask_bytes, mime_type="image/png",
                            width=edit_canvas_width, height=edit_canvas_height,
                        ),
                        role="slot1_edit_mask",
                        label="white = existing glyph silhouette plus up to 3px stitch-foot contact; black = protected original stock",
                ))
                if is_lite or is_direct:
                    layout_bytes = slot1_material.placement_guide_png()
                    references.append(ReferenceImageInput(
                        image=PreparedImage(image_bytes=layout_bytes, mime_type="image/png",
                                            width=edit_canvas_width, height=edit_canvas_height),
                        role="slot1_layout_guide",
                        label="exact locked text, layout, pigments and position on this same crop; REFERENCE ONLY, never edit this flat guide",
                    ))
            elif preview_bytes:
                references.append(ReferenceImageInput(
                    image=(native_thread_macro(preview_bytes) if material_transfer
                           else _prepared_image(preview_bytes)),
                    role=("slot1_thread_material_reference" if material_transfer else "embroidered_front_preview"),
                    label=("byte-verified native Pass A satin thread macro from Slot 1; material only"
                           if material_transfer else "persisted artwork fitted inside calibrated front embroidery area")))
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
                # The two boards share exact HAT 1..N numbering. The first is
                # immutable factory color authority; the second fixes each
                # color's design-to-crown ratio without inventing cap geometry.
                references.append(make_palette_board(stage, multi_color_stock))
                pinned = job_front_placement(context.job.payload.get("front_placement"))
                master_photos = [
                    (key, render_front_reference(
                        stock_photo, design_bytes,
                        guide=tuple(pinned[name] for name in ("left", "top", "width", "height")),
                        design_size=pinned.get("design_size", 100),
                    ))
                    for key, stock_photo in multi_color_stock
                ]
                numbered_master = make_palette_board(stage, master_photos)
                references.append(ReferenceImageInput(
                    image=numbered_master.image,
                    role="embroidered_multicolor_scale_reference",
                    label="HAT 1..N match the original official palette: each color has the same calibrated design-to-crown scale; reference board only, NOT final collage",
                ))
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
        base = (PreparedImage(
            image_bytes=slot1_material.model_edit_target_png(), mime_type="image/png",
            width=slot1_material.edit_canvas_size[0],
            height=slot1_material.edit_canvas_size[1],
        ) if slot1_material is not None else _prepared_image(refs[0][1]))
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
        view_prompt = (LEGACY_EC2_VIEW_PROMPT if stage == "prod_s2_ec2" and legacy_three_quarter
                       else VIEW_PROMPTS[stage])
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
                     else ("EDIT the provided true front stock photograph only within its "
                           "precise embroidery mask. Keep the original camera, color and "
                           "all non-embroidery pixels unchanged. "
                           if stage == "prod_s2_ec1"
                           else ("EXACT PHOTO EDIT, NOT TEXT-TO-IMAGE: The Edit target is the "
                                 "official selected-color 3/4 stock photo. First inspect that "
                                 "image file, then invoke $imagegen in IMAGE-EDIT mode using it "
                                 "as the actual image input. Never create a new hat from a "
                                 "text prompt or front/side composite. If edit-mode image input "
                                 "is unavailable, stop with a blocker rather than synthesize "
                                 "a replacement. Keep framing and all non-embroidery photo "
                                 "content unchanged. "
                                 if stage == "prod_s2_ec2" and not legacy_three_quarter
                                 else "Generate ONE photorealistic commercial ecommerce product photograph "
                                      "of the official Valucap 8869 "
                                      + color_key.replace("-", "/") + " five-panel twill cap. "))))
            + view_prompt
            + (" " + front_placement_prompt(job_front_placement(
                context.job.payload.get("front_placement")))
               if stage != "prod_s2_ec4" and (stage != "prod_s2_ec2" or legacy_three_quarter) else "")
            + " " + embroidery_prompt_for_product_slot(stage)
            + (" SLOT 1 ONLY: photographic image-edit workflow: the square-padded stock is the target; "
               "the original design appears as a flat pigment guide, NOT a final "
               "stitch render; the model must produce real fine yarn fibers itself. "
               "Keep the padded stock framing fixed without moving or scaling. "
               "The white of slot1_edit_mask is the only editable thread region; "
               "black must remain identical to unaltered_stock_reference. "
               "Do not draw guide lines, rewrite typography, change camera aspect "
               "or reconstruct the cap. Keep stitch fibers subtle, thread pigment "
               "accurate and avoid foam/emboss/printed/decal appearance. "
               "The backend will composite actual model-generated thread within the original silhouette, reject no-op edits, and preserve every stock pixel outside the mask "
               "and strictly retain the original design silhouette. "
               if stage == "prod_s2_ec1" else "")
            + (" V4 THREAD CONTACT: Render minor physical twill compression directly under stitch feet and gentle crown-curve-aware highlights. Allow only a hairline fabric darkening immediately beside individual thread edges; do not draw a continuous dark outline or an embossed groove. The worker will clip this contact to a protected 2px band, keep original weave and exact lettering, and restore every other hat pixel. " if stage == "prod_s2_ec1" and skill_name == "gatorhats-8869-ecommerce-front-thread-v4" else "")
            + " Keep all cap panel seams, fabric, bill curvature, selected-color fabric "
            "colors and hardware strictly consistent with the official reference photos. "
            "The approved GatorHats side branding is allowed only on the visible side of the cap; no unrelated logos, added decorations, invented slogans or merged panels."
        )
        if is_lite:
            # Actual fabric-first two-pass EDIT; person is the UNPRINTED
            # original cap ROI. The separate guide freezes text layout/colors.
            # No legacy v4 prompt stacking or synthetic underlay as target.
            prompt = (
                "ThreadTransfer Lite, two native IMAGE-EDIT calls only. "
                "Pass A: edit design_reference into a realistic close-up of that EXACT "
                "typography sewn directly onto neutral twill, with directional fine "
                "satin and tatami yarn. Pass B: INPAINT embroidery onto person, "
                "the UNPRINTED original factory cap crown crop. The role "
                "slot1_layout_guide is an exact spatial/color stencil reference "
                "and must NEVER be used as the edit target. Transfer only A's "
                "yarn material, not its fabric or letter geometry; use the "
                "original stock twill and curved lighting from person. "
                "No changed cap pixels outside slot1_edit_mask, extra letters, "
                "flat paint, hard outline, foam, rubber or 3D emboss. "
                "Preserve all original words/colors/position. Two images, no extras."
            )
        if builtin_stock:
            if not any(ref.role == "slot1_thread_material_reference" for ref in references):
                raise EcommerceError("ecommerce_thread_material_missing",
                                     "Native Pass A macro is missing from pinned Slot 1.")
            prompt = "Built-in original factory stock inpaint using pinned native Slot 1 thread material."
        if is_threadtransfer:
            prompt = ("Generate a faithful photographic close-up of the selected typography "
                      "embroidered with fine yarn on neutral fabric. EXACT spelling, color "
                      "and letter arrangement; no graphic frame or photo labels. "
                      "This is the only native ImageGen call. ThreadTransfer will locally "
                      "separate the yarn from the fabric and composite onto original cap.")
        elif is_reference:
            # Historical queued V2.3 jobs keep their native two-step workflow.
            prompt = "Slot 1 built-in fine-thread reference then original-stock inpaint."
        elif is_direct_legacy:
            # Preserve the earlier pinned single-edit behavior only for
            # already-queued v1 jobs; do not silently upgrade in flight.
            prompt = "Slot 1 built-in direct embroidery photo edit; protect unaltered stock."
        binary = str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex")
        primary_home = str(getattr(settings, "CODEX_IMAGE_HOME",
                                   "/var/lib/creative-asset-manager/codex"))
        material_reference_remote_id: str | None = None
        material_reference_bytes: bytes | None = None
        if (is_threadtransfer and preview_remote_id
                and preview_design_sha == design_sha
                and context.job.payload.get("regenerate_material_reference") is not True):
            # Saved reference is sufficient. Do not even acquire a Codex
            # account/lease for Retry or a completed-job reapplication.
            generated, material_reference_remote_id, _material_size = (
                await self._checkpointed_slot1_generate(
                    context=context, storage=storage, runner=None,
                    person=base, references=references, prompt=prompt,
                    source_revision=plan.source_revision,
                    design_sha=design_sha, folder_id=folder_id,
                    preview_remote_id=preview_remote_id, preview_mime=preview_mime,
                    preview_size=preview_size, preview_design_sha=preview_design_sha,
                    transfer_material=slot1_material,
                    transfer_design_size=job_front_placement(
                        context.job.payload.get("front_placement")
                    ).get("design_size", 100),
                )
            )
        elif builtin_back:
            with Image.open(BytesIO(refs[0][1])) as unprinted_back:
                back_mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(unprinted_back.format)
            if not back_mime:
                raise EcommerceError("ecommerce_stock_invalid", "Unsupported official back stock.")
            generated = GeneratedImageResult(provider="codex", model=None,
                                             image_bytes=refs[0][1], mime_type=back_mime,
                                             provider_metadata={"native_passes": 0})
        else:
          with context.dependencies.session_factory() as pool_session:
            with acquire_worker_profile(pool_session, context.job.tenant_id, "stage2",
                                         primary_home, binary,
                                         None if is_direct or builtin_stock else skill_name) as (account_id, selected_home):
                pool_session.commit()
                runner = CodexImageGenRunner(CodexImageRunnerConfig(
                    binary=binary, codex_home=selected_home,
                    staging_root=str(getattr(settings, "IMAGE_GENERATION_STAGING_ROOT",
                                            "/var/lib/creative-asset-manager/image-generation")),
                    skill_name=skill_name,
                    output_contract=("slot1_reference_inpaint_v2" if is_reference else
                                     "slot1_direct_inpaint" if is_direct_legacy else
                                     "slot1_two_pass" if is_lite else
                                     "stage2_stock_inpaint_single" if builtin_stock else "single_png"),
                    timeout_seconds=int(getattr(settings, "CODEX_IMAGE_TIMEOUT_SECONDS", 900)),
                    model=str(getattr(settings, "CODEX_IMAGE_MODEL", "") or "").strip() or None,
                ))
                try:
                    if checkpointed_reference:
                        generated, material_reference_remote_id, _material_size = (
                            await self._checkpointed_slot1_generate(
                                context=context, storage=storage, runner=runner,
                                person=base, references=references, prompt=prompt,
                                source_revision=plan.source_revision,
                                design_sha=design_sha, folder_id=folder_id,
                                preview_remote_id=preview_remote_id,
                                preview_mime=preview_mime, preview_size=preview_size,
                                preview_design_sha=preview_design_sha,
                                transfer_material=slot1_material if is_threadtransfer else None,
                                transfer_design_size=job_front_placement(
                                    context.job.payload.get("front_placement")
                                ).get("design_size", 100),
                            )
                        )
                    else:
                        generated = await runner.generate_from_references(
                            attempt_id=context.job.entity_id, person=base,
                            references=references, prompt=prompt,
                        )
                finally:
                    runner.cleanup_attempt(context.job.entity_id)
                if material_transfer and stage in SCENE_STAGE_SLOTS:
                    # Scene Skill owns composition only. Native Pass B refines
                    # actual thread on its finished photo without reshooting it.
                    from app.modules.realistic_review_ugc.stage2_stock_inpaint import (
                        verified_inpaint_result,
                    )
                    inpaint_refs = [ref for ref in references if ref.role in {
                        "design_reference", "slot1_thread_material_reference"}]
                    if len(inpaint_refs) != 2:
                        raise EcommerceError("ecommerce_thread_material_missing",
                                             "The native embroidery macro was not pinned.")
                    scene_refiner = CodexImageGenRunner(CodexImageRunnerConfig(
                        binary=binary, codex_home=selected_home,
                        staging_root=str(getattr(settings, "IMAGE_GENERATION_STAGING_ROOT",
                                                "/var/lib/creative-asset-manager/image-generation")),
                        skill_name=SLOT1_DIRECT_NAME,
                        output_contract="stage2_scene_inpaint_single",
                        timeout_seconds=int(getattr(settings, "CODEX_IMAGE_TIMEOUT_SECONDS", 900)),
                        model=str(getattr(settings, "CODEX_IMAGE_MODEL", "") or "").strip() or None,
                    ))
                    scene_before = generated
                    try:
                        generated = await scene_refiner.generate_from_references(
                            attempt_id=context.job.entity_id + "-thread-inpaint",
                            person=_prepared_image(scene_before.image_bytes),
                            references=inpaint_refs,
                            prompt="Inpaint only visible machine-embroidered lettering, leaving entire scene intact.",
                        )
                        verified_inpaint_result(scene_before.image_bytes, generated, "scene")
                        generated = GeneratedImageResult(
                            provider="codex", model=generated.model, image_bytes=generated.image_bytes,
                            mime_type=generated.mime_type,
                            provider_metadata={
                                **(generated.provider_metadata or {}),
                                "native_passes": 2,
                                "scene_base_sha256": hashlib.sha256(scene_before.image_bytes).hexdigest(),
                                "thread_reference_sha256": hashlib.sha256(
                                    inpaint_refs[1].image.image_bytes
                                    if inpaint_refs[1].role == "slot1_thread_material_reference"
                                    else inpaint_refs[0].image.image_bytes).hexdigest(),
                            },
                        )
                    finally:
                        scene_refiner.cleanup_attempt(context.job.entity_id + "-thread-inpaint")
        # V2.4 Slot 1 composites verified photographic reference pixels
        # directly onto stock. Historical native Pass B output still passes
        # strict geometry/color QA before its original protected composite.
        # QA failure retries the image job; it never stores a fake success.
        output_bytes = generated.image_bytes
        output_mime = generated.mime_type
        if builtin_stock:
            from app.modules.realistic_review_ugc.stage2_stock_inpaint import (
                Stage2InpaintError, composite_original_stock,
            )
            try:
                output_bytes = composite_original_stock(refs[0][1], generated, stage)
            except Stage2InpaintError as exc:
                raise RrugcGenerationHandlerError(
                    "ecommerce_thread_inpaint_qa_failed", str(exc), retryable=False,
                ) from exc
            output_mime = "image/png"
        if slot1_material is not None and not is_threadtransfer:
            try:
                output_bytes = finish_slot1_refinement(
                    slot1_material, generated.image_bytes,
                    clip_raw_halo=is_lite or is_direct,
                    require_raw_stock=is_direct,
                    fine_thread_profile=is_reference,
                )
            except Slot1EmbroideryQAError as exc:
                raise RrugcGenerationHandlerError(
                    _lite_slot1_qa_error_code(exc) if is_lite or is_direct else "ecommerce_slot1_material_qa_failed",
                    str(exc),
                    retryable=not (is_lite or is_direct),  # Do not spend native image calls retrying visual QA.
                ) from exc
            output_mime = "image/png"
        try:
            if len(output_bytes) > MAX_REFERENCE_BYTES:
                raise ValueError("Output too large")
            with Image.open(BytesIO(output_bytes)) as image:
                width, height = image.size
                expected = {"PNG":"image/png", "JPEG":"image/jpeg", "WEBP":"image/webp"}.get(image.format)
                if expected != output_mime or width < 512 or height < 512 or width * height > MAX_REFERENCE_PIXELS:
                    raise ValueError("Output dimensions or media type invalid")
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise RrugcGenerationHandlerError("ecommerce_output_invalid",
                            "Generated photo did not pass basic validation.", retryable=True) from exc
        # V2.4 locally transfers saved Pass A; V2.3 uses checkpointed native
        # Pass B. Pinned V2.2 jobs still use the historical completion path.
        if is_reference and not checkpointed_reference:
            if len(generated.additional_images) != 1:
                raise EcommerceError("ecommerce_thread_material_missing",
                                     "Native Slot 1 material was not returned.")
            material_reference_bytes = generated.additional_images[0]
            material_hash = hashlib.sha256(material_reference_bytes).hexdigest()
            if (material_hash != generated.provider_metadata.get("reference_sha256")
                    or len(material_reference_bytes) > MAX_REFERENCE_BYTES):
                raise EcommerceError("ecommerce_thread_material_unverified",
                                     "Unverified or oversize native embroidery material.")
            with Image.open(BytesIO(material_reference_bytes)) as macro_image:
                if macro_image.format != "PNG" or min(macro_image.size) < 512:
                    raise EcommerceError("ecommerce_thread_material_invalid", "Invalid embroidery material.")
                macro_image.verify()
            material_asset = await storage.store_asset(StoreAssetInput(
                tenant_id=context.job.tenant_id,
                content_hash=material_hash, body=_bytes_body(material_reference_bytes),
                asset_id="rrugc-ecommerce-thread-material:" + context.job.entity_id,
                content_type="image/png", size_bytes=len(material_reference_bytes),
                filename=f"input_thread_material_{context.job.entity_id}.png",
                destination_folder_id=folder_id or None,
            ))
            if not material_asset.remote_file_id:
                raise StorageProviderError("Native embroidery reference could not be saved.",
                                           retryable=True, code="ecommerce_thread_material_storage_failed")
            material_reference_remote_id = material_asset.remote_file_id
        stored = await storage.store_asset(StoreAssetInput(
            tenant_id=context.job.tenant_id,
            content_hash=hashlib.sha256(output_bytes).hexdigest(),
            body=_bytes_body(output_bytes),
            asset_id="rrugc-ecommerce:" + context.job.entity_id + ":" + context.job.id,
            content_type=output_mime, size_bytes=len(output_bytes),
            filename=f"output_ecommerce_{stage}_{context.job.entity_id}_{context.job.id}{_output_extension(output_mime)}",
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
            if material_reference_remote_id and not checkpointed_reference:
                # Historical V2.2 completion: V2.3 already pinned the durable
                # Pass A checkpoint before calling Pass B.
                row.front_preview_remote_file_id = material_reference_remote_id
                row.front_preview_content_type = "image/png"
                row.front_preview_size_bytes = len(material_reference_bytes)
                row.front_preview_design_sha256 = design_sha
            save_output_version(session, tenant_id=context.job.tenant_id, stage="ecommerce",
                                job=row, remote_file_id=stored.remote_file_id,
                                content_type=output_mime,
                                size_bytes=len(output_bytes), width=width, height=height,
                                processing_job_id=context.job.id,
                                font_info={**({"slot1_embroidery_pipeline": {
                                    "version": ("threadtransfer-v1-raster"
                                                if is_threadtransfer else
                                                "thread-reference-inpaint-v2.3-checkpointed"
                                                if checkpointed_reference else
                                                "thread-reference-inpaint-v2" if is_reference else
                                                "direct-inpaint-v1" if is_direct_legacy else
                                                "threadtransfer-lite-v1" if is_lite else
                                                "ai-material-localized-v3"),
                                    **({"native_passes": generated.provider_metadata["native_passes"],
                                        "edited_sha256": generated.provider_metadata["edited_sha256"],
                                        **({"macro_sha256": generated.provider_metadata["macro_sha256"]}
                                           if is_lite else {}),
                                        **({"reference_sha256": generated.provider_metadata["reference_sha256"]}
                                           if is_reference else {})}
                                       if is_lite or is_direct else {}),
                                    "outside_stock_preserved": True,
                                    "flat_preview_not_used_for_refinement": True,
                                    **({"thread_layer_sha256": generated.provider_metadata.get("thread_layer_sha256"),
                                        "transfer_box": generated.provider_metadata.get("transfer_box"),
                                        "inference_calls": generated.provider_metadata.get("native_passes")}
                                       if is_threadtransfer else {}),
                                    **({"thread_material_asset": material_reference_remote_id}
                                       if material_reference_remote_id else {}),
                                    **({"material_revision": ("fine-thread-reference-v2" if is_reference else
                                                               "single-native-edit-v1" if is_direct_legacy else
                                                               "roi-original-stock-inpaint-v1.3"),
                                        "edit_target": "original_unprinted_stock",
                                        "layout_role": "slot1_layout_guide",
                                        "native_front_roi": list(slot1_material.edit_roi_box),
                                        "edit_canvas_size": list(slot1_material.edit_canvas_size),
                                        "contact_width_px": slot1_material.contact_width_px}
                                       if (is_lite or is_direct) and slot1_material.edit_roi_box else {}),
                                }} if stage == "prod_s2_ec1" else {}),
                                **({"thread_inpaint_pipeline": {
                                    "version": "reference-reuse-v1",
                                    "material_remote_file_id": row.front_preview_remote_file_id,
                                    "material_source": "slot1-native-pass-a",
                                    "native_sha256": generated.provider_metadata.get("native_sha256"),
                                    "original_stock_pixels_protected": bool(builtin_stock or builtin_back),
                                    "scene_photo_preserved_by_edit_gate": bool(stage in SCENE_STAGE_SLOTS),
                                }} if material_transfer else {}),
                                "input_reference_snapshot": {
                                    "source_revision": row.source_revision,
                                    "stock_sha256": row.stock_sha256,
                                    "stock_input_roles": [name for name, _ in refs],
                                    "source_three_quarter_photo": (
                                        stage == "prod_s2_ec2" and not legacy_three_quarter),
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
            row.output_content_type = output_mime
            row.output_size_bytes = len(output_bytes)
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