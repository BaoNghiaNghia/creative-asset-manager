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
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import OpenStoredAssetInput, StoreAssetInput, StorageProviderError
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.codex_account_manager import CodexAccountPoolBusy, acquire_worker_profile
from app.modules.realistic_review_ugc.generation_handler import (
    RrugcGenerationHandlerError, _bytes_body, _output_extension, _prepared_image,
    MAX_REFERENCE_BYTES, MAX_REFERENCE_PIXELS,
)
from app.modules.image_generation.providers import ReferenceImageInput
from app.modules.realistic_review_ugc.model import (
    RrugcColorwayJobModel, RrugcSourcePlanModel, RrugcEcommerceJobModel,
    RrugcVisorAccentSelectionModel,
)
from app.modules.realistic_review_ugc.skill_registry import ensure_skill_registry, assert_skill_enabled
from app.modules.realistic_review_ugc.output_versions import save_output_version
from app.modules.realistic_review_ugc.embroidery_realism import embroidery_prompt_for_product_slot
from app.modules.realistic_review_ugc.ecommerce_front_reference import render_front_reference
from app.modules.realistic_review_ugc.front_placement import (
    front_placement_prompt, job_front_placement, pin_front_placement,
)
from app.modules.realistic_review_ugc.source_plans import RRUGC_SOURCE_ROOT_FOLDER_ID
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillRegistryError, resolve_stage2_skill, verify_stage2_skill_runtime,
    installed_stage2_skill_sha256,
)
from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError,
    SLOT1_DIRECT_NAME, SLOT1_DIRECT_VERSION, SLOT1_DIRECT_SHA256,
    SLOT1_DIRECT_PREVIOUS_VERSION, SLOT1_DIRECT_PREVIOUS_SHA256,
    SLOT1_DIRECT_CHECKPOINTED_VERSION, SLOT1_DIRECT_CHECKPOINTED_SHA256,
)
from app.modules.image_generation.providers import PreparedImage, GeneratedImageResult
from app.modules.realistic_review_ugc.slot1_embroidery import (
    build_slot1_material_pass, finish_slot1_refinement, Slot1EmbroideryQAError,
)
from app.modules.realistic_review_ugc.stage2_stock_inpaint import verified_inpaint_result, Stage2InpaintError
from app.modules.realistic_review_ugc.threadtransfer import (
    composite_reference_on_cap, _layer_from_reference,
)
from app.modules.realistic_review_ugc.visor_accent import (
    VERSION as VISOR_ACCENT_VERSION, AccentSelection, VisorAccentError,
    select_accent, recolor_layer, VISOR_THREAD_RGB,
)

# Distinct synthetic source namespace for explicitly selected Products Stage 1 PNGs.
# Reviews Stage 2 uses real Drive source files and the embroidery_ prefix.
PRODUCTS_STAGE1_SELECTED_ROOT = "cam-gatorhats-products-stage1-selected"
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
# Products Slot 7 uses the twelve updated Natural-series cap photos.
# The legacy Khaki/Maroon remains in historical records and Reviews Stage 2,
# but is never enqueued or shown as a current Products Slot 7 output.
PRODUCTS_COLORS = tuple((key, label) for key, label in COLORS if key.startswith("natural-"))
PRODUCTS_COLOR_KEYS = frozenset(key for key, _ in PRODUCTS_COLORS)
assert len(PRODUCTS_COLORS) == 12
MAX_MANUAL_RETRIES = 3
PRODUCTS_VISOR_ACCENT_PIPELINE = "v3-visor-accent"
assert set(VISOR_THREAD_RGB) == PRODUCTS_COLOR_KEYS


class ColorwayError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _stock_path(settings: Settings, color_key: str) -> Path:
    if color_key not in dict(COLORS):
        raise ColorwayError("colorway_unknown_color", "Unknown official stock color.", 422)
    # Use complete, administrator-imported official stock for future jobs only;
    # never modify completed images or retroactively requeue their jobs.
    from app.modules.realistic_review_ugc.hat_stock_sync import read_managed_stock
    from app.modules.realistic_review_ugc.slot7_front_stock import (
        configured_slot7_front, read_slot7_front,
    )
    imported = read_managed_stock(settings, color_key, "front")
    if imported is not None:
        return imported[0]
    # Only Slot 7 may use individually verified manufacturer front photos.
    # The shared stock catalog's 3-angle coverage is never misrepresented.
    supplier_front = read_slot7_front(settings, color_key)
    if supplier_front is not None:
        return supplier_front[0]
    if configured_slot7_front(settings, color_key):
        raise ColorwayError("colorway_stock_invalid",
                            "The pinned Slot 7 stock photo is missing or corrupt.", 503)
    # Fallback remains the installed verified stock if no verified front exists.
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


def _codex_runtime_available(settings: Settings, *, require_skill: bool = True) -> bool:
    """Cheap readiness gate. Provider login/quota still require a live smoke."""
    binary = str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex")
    home = Path(str(getattr(settings, "CODEX_IMAGE_HOME",
                            "/var/lib/creative-asset-manager/codex"))).resolve()
    return bool(
        shutil.which(binary)
        and (not require_skill or (home / "skills" / DEFAULT_COLORWAY_SKILL / "SKILL.md").is_file())
    )


def _product_material_ref(session: Session, tenant_id: str, source_plan_id: str,
                          source_revision: str, preferred_color: str = "natural-black") -> dict:
    """Reuse one completed native Slot 1 material for all Products hat colors.

    Only tenant/source-revision matched, finished V2 image edits qualify.
    Reviews Stage 2 never calls this helper.
    """
    rows = list(session.scalars(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.tenant_id == tenant_id,
        RrugcEcommerceJobModel.source_plan_id == source_plan_id,
        RrugcEcommerceJobModel.source_revision == source_revision,
        RrugcEcommerceJobModel.stage == "prod_s2_ec1",
        RrugcEcommerceJobModel.status == "completed",
        RrugcEcommerceJobModel.skill_name == SLOT1_DIRECT_NAME,
        RrugcEcommerceJobModel.front_preview_remote_file_id.is_not(None),
        RrugcEcommerceJobModel.front_preview_design_sha256.is_not(None),
    ).order_by(RrugcEcommerceJobModel.completed_at.desc()).limit(20)))
    front = next((row for row in rows if row.color_key == preferred_color), None)
    if front is None:
        front = rows[0] if rows else None
    if front is None:
        raise ColorwayError("colorway_thread_reference_missing",
                            "Complete or regenerate Products Slot 1 first to save its native embroidery macro.")
    return {
        "material_remote_file_id": front.front_preview_remote_file_id,
        "material_content_type": front.front_preview_content_type,
        "material_size_bytes": front.front_preview_size_bytes,
        "material_design_sha256": front.front_preview_design_sha256,
        "material_source_job_id": front.id,
    }


def _get_or_create_visor_selection(
    session_factory, *, tenant_id: str, source_plan_id: str,
    source_revision: str, material_remote_file_id: str,
    reference_png: bytes, thread_layer: Image.Image,
) -> tuple[AccentSelection, bool]:
    """Persist one immutable ML mask per source material across 12 parallel workers.

    Lock the shared source-plan row, NOT each color job: PostgreSQL serializes
    different colorway workers and prevents redundant selection/model passes.
    """
    digest = hashlib.sha256(reference_png).hexdigest()
    with session_factory() as session:
        source = session.scalar(select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == tenant_id,
            RrugcSourcePlanModel.id == source_plan_id,
            RrugcSourcePlanModel.source_revision == source_revision,
            RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
        ).with_for_update())
        if source is None:
            raise ColorwayError("colorway_accent_source_changed", "Pinned design is no longer valid.")
        existing = session.scalar(select(RrugcVisorAccentSelectionModel).where(
            RrugcVisorAccentSelectionModel.tenant_id == tenant_id,
            RrugcVisorAccentSelectionModel.source_plan_id == source_plan_id,
            RrugcVisorAccentSelectionModel.source_revision == source_revision,
            RrugcVisorAccentSelectionModel.material_remote_file_id == material_remote_file_id,
            RrugcVisorAccentSelectionModel.algorithm_version == VISOR_ACCENT_VERSION,
        ))
        if existing:
            if (existing.reference_sha256 != digest
                    or existing.mask_sha256 != hashlib.sha256(existing.mask_png).hexdigest()):
                raise ColorwayError("colorway_accent_cache_changed", "Saved accent mask evidence changed.")
            return AccentSelection(
                mask_png=existing.mask_png, method=existing.selection_method,
                selected_pixels=existing.selected_pixels,
                thread_pixels=existing.thread_pixels,
                layer_sha256=existing.layer_sha256,
            ), True
        selected = select_accent(thread_layer)
        session.add(RrugcVisorAccentSelectionModel(
            tenant_id=tenant_id, source_plan_id=source_plan_id,
            source_revision=source_revision, material_remote_file_id=material_remote_file_id,
            reference_sha256=digest, algorithm_version=VISOR_ACCENT_VERSION,
            mask_png=selected.mask_png,
            mask_sha256=hashlib.sha256(selected.mask_png).hexdigest(),
            layer_sha256=selected.layer_sha256,
            selection_method=selected.method,
            selected_pixels=selected.selected_pixels,
            thread_pixels=selected.thread_pixels,
        ))
        session.commit()
        return selected, False


class ColorwayService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def readiness(self, workflow: str = "reviews") -> dict:
        """Stock preflight uses twelve Products images, thirteen Reviews images."""
        if workflow not in ("products", "reviews"):
            raise ColorwayError("colorway_workflow_invalid", "Unknown GatorHats workflow.", 422)
        selected_colors = PRODUCTS_COLORS if workflow == "products" else COLORS
        configured = all((
            self.settings.PROCESSING_JOBS_ENABLED,
            self.settings.IMAGE_GENERATION_ENABLED,
            self.settings.MANAGED_ASSET_STORAGE_ENABLED,
        )) and (workflow == "products" or self.settings.CODEX_IMAGE_GENERATION_ENABLED)
        available_colors = 0
        for color_key, _ in selected_colors:
            try:
                _stock_bytes(self.settings, color_key)
                available_colors += 1
            except (ColorwayError, OSError):
                continue
        # Products use the built-in ImageGen contract without a Skill ZIP.
        # Reviews retain their historical installed-Skill readiness gate.
        # Products Slot 7 transfers pinned real thread using local raster;
        # unlike Reviews it needs no Codex binary or free account quota.
        runtime_ok = _codex_runtime_available(self.settings) if workflow == "reviews" else True
        error_code = (
            "colorway_disabled" if not configured else
            "colorway_stock_missing" if available_colors != len(selected_colors) else
            "colorway_runtime_missing" if not runtime_ok else None
        )
        return {
            "ready": error_code is None,
            "error_code": error_code,
            "stock_ready_count": available_colors,
            "stock_total_count": len(selected_colors),
        }

    def _row(self, tenant_id: str, job_id: str) -> RrugcColorwayJobModel | None:
        return self.session.scalar(select(RrugcColorwayJobModel).where(
            RrugcColorwayJobModel.tenant_id == tenant_id, RrugcColorwayJobModel.id == job_id,
        ).with_for_update())

    def _enqueue(self, row: RrugcColorwayJobModel, *, front_placement=None,
                 pinned_material: dict | None = None) -> None:
        sequence = int(self.session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == row.tenant_id,
            ProcessingJobModel.entity_type == "rrugc_colorway_job",
            ProcessingJobModel.entity_id == row.id,
        )) or 0)
        if row.skill_source == "internal" and row.skill_name == SLOT1_DIRECT_NAME:
            material_ref = pinned_material or _product_material_ref(
                self.session, row.tenant_id, row.source_plan_id, row.source_revision)
        else:
            material_ref = None
        processing = ProcessingRepository(self.session, self.settings).create_job(
            tenant_id=row.tenant_id, job_type=JOB_TYPE, entity_type="rrugc_colorway_job",
            entity_id=row.id, idempotency_key=f"rrugc-colorway:{row.id}:{sequence}",
            payload={"colorway_job_id": row.id,
                     **({"thread_material_pipeline": PRODUCTS_VISOR_ACCENT_PIPELINE,
                         **material_ref} if material_ref else {}),
                     **({"front_placement": front_placement} if front_placement is not None else {})},
            priority=20, max_attempts=3,
            provider_key=None if material_ref else "codex",
            provider_scope=None if material_ref else "ai",
        )
        row.processing_job_id = processing.id

    def queue(self, *, tenant_id: str, user_id: str, source_plan_ids: list[str],
              workflow: str = "reviews",
              skill_source: str | None = None, skill_id: str | None = None,
              skill_name: str | None = None, skill_version: str | None = None) -> dict:
        ids = list(dict.fromkeys(source_plan_ids))
        if workflow not in ("products", "reviews"):
            raise ColorwayError("colorway_workflow_invalid", "Unknown GatorHats workflow.", 422)
        if not ids or len(ids) > 50:
            raise ColorwayError("colorway_batch_size", "Choose between 1 and 50 designs per request.", 422)
        if not all((
            self.settings.PROCESSING_JOBS_ENABLED,
            self.settings.IMAGE_GENERATION_ENABLED,
            self.settings.MANAGED_ASSET_STORAGE_ENABLED,
        )) or (workflow == "reviews" and not self.settings.CODEX_IMAGE_GENERATION_ENABLED):
            raise ColorwayError(
                "colorway_disabled",
                "Stage 2 colorway generation is not enabled on the server.",
                503,
            )
        try:
            if workflow == "products":
                from types import SimpleNamespace
                skill = SimpleNamespace(source="internal", skill_id=None,
                                        skill_name=SLOT1_DIRECT_NAME, skill_version=SLOT1_DIRECT_VERSION)
            else:
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

        # Products: only the twelve updated Natural photos. Reviews retain
        # thirteen historical colors. Never validate/queue retired Products stock.
        selected_colors = PRODUCTS_COLORS if workflow == "products" else COLORS
        stock_digests = {key: hashlib.sha256(_stock_bytes(self.settings, key)).hexdigest() for key, _ in selected_colors}
        plans = list(self.session.scalars(select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == tenant_id, RrugcSourcePlanModel.id.in_(ids),
        ).order_by(RrugcSourcePlanModel.id).with_for_update()))
        if len(plans) != len(ids) or any(
            p.status == "missing"
            or (workflow == "products" and p.root_folder_id != PRODUCTS_STAGE1_SELECTED_ROOT)
            or (workflow == "reviews" and (
                p.root_folder_id != RRUGC_SOURCE_ROOT_FOLDER_ID
                or not p.source_name.casefold().startswith("embroidery_")
            ))
            or p.source_mime_type.casefold() not in {"image/jpeg", "image/png"}
            or not p.source_name.casefold().endswith((".jpg", ".jpeg", ".png"))
            or (workflow == "products" and not p.source_name.casefold().endswith(".png"))
            or p.source_name.casefold().startswith("output_") for p in plans
        ):
            raise ColorwayError("colorway_source_invalid",
                                "Only available JPG/PNG design files from the configured Drive source can be generated.", 422)
        if workflow == "products":
            # Fail before queuing any color if native material is absent.
            for plan in plans:
                _product_material_ref(self.session, tenant_id, plan.id, plan.source_revision)
        existing = {(j.source_plan_id, j.source_revision, j.color_key) for j in self.session.scalars(
            select(RrugcColorwayJobModel).where(
                RrugcColorwayJobModel.tenant_id == tenant_id,
                RrugcColorwayJobModel.source_plan_id.in_(ids),
            )
        )}
        count = 0
        for plan in plans:
            for color_key, _ in selected_colors:
                if (plan.id, plan.source_revision, color_key) in existing:
                    continue
                row = RrugcColorwayJobModel(
                    id=str(uuid4()), tenant_id=tenant_id, source_plan_id=plan.id,
                    source_revision=plan.source_revision, color_key=color_key,
                    stock_sha256=stock_digests[color_key],
                    skill_source=skill.source, skill_id=skill.skill_id,
                    skill_name=skill.skill_name, skill_version=skill.skill_version,
                    skill_bundle_sha256=(SLOT1_DIRECT_SHA256 if workflow == "products" else
                                         installed_stage2_skill_sha256(skill.skill_name, settings=self.settings)),
                    created_by_user_id=user_id, status="queued",
                )
                self.session.add(row)
                self.session.flush()
                self._enqueue(row, front_placement=(
                    pin_front_placement(self.session, tenant_id, plan.source_revision)
                    if workflow == "products" else None))
                count += 1
        self.session.commit()
        return {"queued": count, "existing": len(ids) * len(selected_colors) - count}

    def regenerate(self, *, tenant_id: str, job_id: str) -> RrugcColorwayJobModel:
        row = self._row(tenant_id, job_id)
        if row is None:
            raise ColorwayError("colorway_not_found", "Colorway job not found.", 404)
        if row.status != "completed" or not row.output_remote_file_id:
            raise ColorwayError("colorway_regenerate_unavailable", "Regenerate only completed colorway jobs.")
        plan = self.session.scalar(select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == tenant_id,
            RrugcSourcePlanModel.id == row.source_plan_id,
        ))
        if (plan is None or plan.status == "missing"
                or plan.source_revision != row.source_revision):
            raise ColorwayError("colorway_source_changed",
                                "Source design changed; cannot regenerate stale output.")
        if plan.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT and row.color_key not in PRODUCTS_COLOR_KEYS:
            raise ColorwayError("colorway_retired_product_stock", "This old cap color is retired from Products Slot 7.")
        # Regenerate is the explicit opt-in moment for a new stock revision.
        # Retry, in contrast, must keep the original SHA pinned.
        current_stock_sha = hashlib.sha256(_stock_bytes(self.settings, row.color_key)).hexdigest()
        row.stock_sha256 = current_stock_sha
        if plan.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT:
            row.skill_source, row.skill_id = "internal", None
            row.skill_name, row.skill_version = SLOT1_DIRECT_NAME, SLOT1_DIRECT_VERSION
            row.skill_bundle_sha256 = SLOT1_DIRECT_SHA256
        row.retry_count = 0
        row.status = "queued"
        row.last_error_code = row.last_error_message = None
        row.started_at = None
        current_placement = (pin_front_placement(self.session, tenant_id, plan.source_revision)
                             if plan.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT else None)
        self._enqueue(row, front_placement=current_placement)
        self.session.commit()
        self.session.refresh(row)
        return row

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
        if plan.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT and row.color_key not in PRODUCTS_COLOR_KEYS:
            raise ColorwayError("colorway_retired_product_stock", "This old cap color is retired from Products Slot 7.")
        if hashlib.sha256(_stock_bytes(self.settings, row.color_key)).hexdigest() != row.stock_sha256:
            raise ColorwayError("colorway_stock_changed", "Stock image changed; old job cannot be retried.")
        row.retry_count += 1
        row.status = "queued"
        row.last_error_code = row.last_error_message = None
        if plan.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT:
            # User-initiated retry moves to the local transfer contract;
            # previously queued attempts retain their pinned v1 payload.
            row.skill_source, row.skill_id = "internal", None
            row.skill_name, row.skill_version = SLOT1_DIRECT_NAME, SLOT1_DIRECT_VERSION
            row.skill_bundle_sha256 = SLOT1_DIRECT_SHA256
        previous = self.session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
        old_placement = (previous.payload_json or {}).get("front_placement") if previous else None
        previous_material = {k: v for k, v in ((previous.payload_json or {}) if previous else {}).items()
                             if k in {"material_remote_file_id", "material_content_type",
                                      "material_size_bytes", "material_design_sha256",
                                      "material_source_job_id"}}
        self._enqueue(row, front_placement=old_placement,
                      pinned_material=previous_material or None)
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
                RrugcColorwayJobModel.source_plan_id.in_(ids),
                or_(RrugcSourcePlanModel.root_folder_id != PRODUCTS_STAGE1_SELECTED_ROOT,
                    RrugcColorwayJobModel.color_key.in_(PRODUCTS_COLOR_KEYS))))
        return [
            {"id": row.id, "source_plan_id": row.source_plan_id, "color_key": row.color_key,
             "color_name": dict(COLORS)[row.color_key], "status": effective_status(row, processing),
             "retry_count": row.retry_count,
             "started_at": row.started_at.isoformat() if row.started_at else None,
             "completed_at": row.completed_at.isoformat() if row.completed_at else None,
             "attempt_count": processing.attempt_count if processing else 0,
             "error_code": row.last_error_code or (processing.last_error_code if processing else None),
             "output_url": f"/api/v1/realistic-review-ugc/colorways/{row.id}/output"
             if row.output_remote_file_id else None}
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
        except VisorAccentError as exc:
            self._mark_failure(context, "colorway_accent_qa_failed", str(exc), terminal=True)
            return JobHandlerResult.non_retryable("colorway_accent_qa_failed", str(exc))
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
                and settings.MANAGED_ASSET_STORAGE_ENABLED):
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
            builtin_product = (plan.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT
                               and row.skill_source == "internal"
                               and row.skill_name == SLOT1_DIRECT_NAME)
            local_pinned = (builtin_product and
                            context.job.payload.get("thread_material_pipeline") in
                            ("v2", PRODUCTS_VISOR_ACCENT_PIPELINE))
            if not local_pinned and not getattr(settings, "CODEX_IMAGE_GENERATION_ENABLED", False):
                return JobHandlerResult.non_retryable("colorway_disabled", "Native image generator disabled.")
            if builtin_product:
                pinned = (row.skill_version, row.skill_bundle_sha256)
                if pinned not in {
                    (SLOT1_DIRECT_VERSION, SLOT1_DIRECT_SHA256),
                    (SLOT1_DIRECT_CHECKPOINTED_VERSION, SLOT1_DIRECT_CHECKPOINTED_SHA256),
                    (SLOT1_DIRECT_PREVIOUS_VERSION, SLOT1_DIRECT_PREVIOUS_SHA256),
                }:
                    raise ColorwayError("colorway_inpaint_version_changed", "Pinned built-in pipeline changed.")
            else:
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
            # Copy mode while SQLAlchemy's plan is still attached to the session.
            # Do not dereference the ORM row after the transaction is closed.
            is_products_colorway = plan.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT
            source_revision = plan.source_revision
            material_pin = dict(context.job.payload) if builtin_product else None
            if builtin_product:
                front = session.scalar(select(RrugcEcommerceJobModel).where(
                    RrugcEcommerceJobModel.id == material_pin.get("material_source_job_id"),
                    RrugcEcommerceJobModel.tenant_id == row.tenant_id,
                    RrugcEcommerceJobModel.source_plan_id == plan.id,
                    RrugcEcommerceJobModel.source_revision == plan.source_revision,
                    RrugcEcommerceJobModel.stage == "prod_s2_ec1",
                    RrugcEcommerceJobModel.skill_name == SLOT1_DIRECT_NAME,
                ))
                if (front is None or not front.front_preview_remote_file_id
                        or front.front_preview_remote_file_id != material_pin.get("material_remote_file_id")
                        or front.front_preview_design_sha256 != material_pin.get("material_design_sha256")):
                    raise ColorwayError("colorway_thread_reference_changed", "Pinned Slot 1 material reference is unavailable.")
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
        local_product = builtin_product and context.job.payload.get("thread_material_pipeline") in (
            "v2", PRODUCTS_VISOR_ACCENT_PIPELINE,
        )
        # Local 12-color raster jobs never prepare provider image inputs.
        stock = None if local_product else _prepared_image(stock_data)
        design = None if local_product else _prepared_image(bytes(source_bytes))
        with_visor_accent = local_product and (
            context.job.payload.get("thread_material_pipeline") == PRODUCTS_VISOR_ACCENT_PIPELINE
        )
        slot7_material = None
        if builtin_product:
            if hashlib.sha256(bytes(source_bytes)).hexdigest() != material_pin["material_design_sha256"]:
                raise ColorwayError("colorway_thread_design_changed", "Native material belongs to another design revision.")
            stream = await storage.open_asset(OpenStoredAssetInput(
                tenant_id=context.job.tenant_id,
                asset_id="rrugc-colorway-native-material:" + source_id,
                remote_file_id=material_pin["material_remote_file_id"],
                content_type=material_pin["material_content_type"],
                size_bytes=material_pin["material_size_bytes"],
            ))
            macro = bytearray()
            try:
                async for chunk in stream.body:
                    macro.extend(chunk)
                    if len(macro) > MAX_REFERENCE_BYTES:
                        raise ColorwayError("colorway_thread_material_large", "Material exceeds safe input size.")
            finally:
                await stream.close()
            if not local_product:
                try:
                    from app.modules.realistic_review_ugc.stage2_stock_inpaint import native_thread_macro
                    material_png = native_thread_macro(bytes(macro))
                except Stage2InpaintError as exc:
                    raise ColorwayError("colorway_thread_material_invalid", str(exc)) from exc
            slot7_material = build_slot1_material_pass(
                stock_data, bytes(source_bytes),
                job_front_placement(context.job.payload.get("front_placement")),
                contact_width_px=3, localized_edit=True, clean_soft_shadow=True,
            )
        prompt = (
            "Apply the embroidery from the design reference to the FRONT CENTER of the "
            f"Valucap 8869 five-panel twill cap shown in the base stock photo. Official cap color: {color_name}. "
            "Use the stock photo for the exact cap shape, crown color, bill color and perspective. "
            "Preserve the source artwork lettering, original motif geometry, stitch direction, "
            "relative thread pigment RGB hues and saturated multicolor thread regions exactly. "
            "Do not recolor embroidery to brown/sepia, auto-match the cap brim, flatten stitches "
            "into a printed texture, or erase fine satin/tatami thread grooves. A truly monochrome "
            "reference must stay monochrome, not be artificially colorized. "
            "Do not invent lettering, change logos, change cap color, or add side embroidery. "
            "Generate one realistic, sharp ecommerce product photo with thin direct embroidery. "
            "The stock color and existing artwork identity are immutable."
        )
        # This worker also serves Reviews Stage 2. Apply the new product-only
        # direct-thread finish strictly to Products Slot 7, not Review jobs.
        if is_products_colorway:
            pinned_placement = job_front_placement(context.job.payload.get("front_placement"))
            prompt += " " + front_placement_prompt(pinned_placement)
            prompt += " " + embroidery_prompt_for_product_slot("prod_s2_col")
        references = ([] if local_product else
                      [ReferenceImageInput(image=design, role="design_reference", label="embroidery")])
        if builtin_product and not local_product:
            rw, rh = slot7_material.edit_canvas_size
            stock = PreparedImage(image_bytes=slot7_material.model_edit_target_png(),
                                  mime_type="image/png", width=rw, height=rh)
            references.extend([
                ReferenceImageInput(image=PreparedImage(
                    image_bytes=slot7_material.edit_mask_png(), mime_type="image/png",
                    width=rw, height=rh), role="slot1_edit_mask",
                    label="white thread contact area only; black factory stock protected"),
                ReferenceImageInput(image=PreparedImage(
                    image_bytes=slot7_material.placement_guide_png(), mime_type="image/png",
                    width=rw, height=rh), role="slot1_layout_guide",
                    label="locked text, colors, geometry and placement; not edit target"),
                ReferenceImageInput(image=material_png,
                    role="slot1_thread_material_reference",
                    label="saved native Pass A satin thread reference from Slot 1; material only"),
            ])
            prompt = ("Inpaint the verified native low-relief satin machine-thread material "
                      f"onto ORIGINAL {color_name} factory stock photo. Keep all stock pixels outside "
                      "the embroidery mask. Do not reinterpret lettering or adapt pigment to cap color.")
        elif is_products_colorway and not local_product:
            master = render_front_reference(
                stock_data, bytes(source_bytes),
                guide=tuple(pinned_placement[key] for key in ("left", "top", "width", "height")),
                design_size=pinned_placement.get("design_size", 100),
            )
            references.append(ReferenceImageInput(
                image=_prepared_image(master), role="embroidered_front_preview",
                label="deterministic front embroidery scale and placement master",
            ))
        visor_accent_meta = None
        if local_product:
            try:
                accent_layer = None
                if with_visor_accent:
                    original_thread, _ = _layer_from_reference(bytes(macro))
                    selection, cached = _get_or_create_visor_selection(
                        context.dependencies.session_factory,
                        tenant_id=context.job.tenant_id, source_plan_id=source_id,
                        source_revision=source_revision,
                        material_remote_file_id=material_pin["material_remote_file_id"],
                        reference_png=bytes(macro), thread_layer=original_thread,
                    )
                    accent_layer, visor_accent_meta = recolor_layer(
                        original_thread, selection, color_key,
                        slot7_material.stock, slot7_material.guide_box,
                    )
                    visor_accent_meta["cached_mask"] = cached
                transferred = composite_reference_on_cap(
                    slot7_material, bytes(macro),
                    design_size=job_front_placement(
                        context.job.payload.get("front_placement")
                    ).get("design_size", 100),
                    thread_layer=accent_layer,
                )
            except Slot1EmbroideryQAError as exc:
                raise RrugcGenerationHandlerError(
                    "colorway_threadtransfer_qa_failed", str(exc),
                    retryable=False,
                ) from exc
            generated = GeneratedImageResult(
                provider="codex", model=None, image_bytes=transferred.output_png,
                mime_type="image/png", provider_metadata={
                    "transfer_engine": ("visor-accent-v1-raster" if with_visor_accent
                                        else "threadtransfer-v2-colorway"),
                    "native_passes": 0,
                    **({"visor_accent": visor_accent_meta} if visor_accent_meta else {}),
                    "thread_material_remote_file_id": material_pin["material_remote_file_id"],
                    "reference_sha256": hashlib.sha256(bytes(macro)).hexdigest(),
                    "thread_layer_sha256": transferred.thread_layer_sha256,
                    "transfer_box": list(transferred.target_box),
                    "stock_outside_thread_preserved": True,
                },
            )
        else:
            binary = str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex")
            primary_home = str(getattr(settings, "CODEX_IMAGE_HOME", "/var/lib/creative-asset-manager/codex"))
            try:
                with context.dependencies.session_factory() as pool_session:
                    with acquire_worker_profile(
                        pool_session, context.job.tenant_id, "stage2",
                        primary_home, binary, None if builtin_product else skill_name,
                    ) as (account_id, selected_home):
                        pool_session.commit()
                        context.logger.info("rrugc_codex_pool_dispatch", extra={
                            "stage": "stage2", "account_id": account_id,
                            "job_id": context.job.entity_id, "tenant_id": context.job.tenant_id,
                        })
                        runner = CodexImageGenRunner(CodexImageRunnerConfig(
                            binary=binary,
                            codex_home=selected_home,
                            staging_root=str(getattr(settings, "IMAGE_GENERATION_STAGING_ROOT", "/var/lib/creative-asset-manager/image-generation")),
                            skill_name=skill_name,
                            output_contract=("stage2_stock_inpaint_single" if builtin_product else "single_png"),
                            timeout_seconds=int(getattr(settings, "CODEX_IMAGE_TIMEOUT_SECONDS", 900)),
                            model=str(getattr(settings, "CODEX_IMAGE_MODEL", "")).strip() or None,
                        ))
                        try:
                            generated = await runner.generate_from_references(
                                attempt_id=context.job.entity_id, person=stock,
                                references=references,
                                prompt=prompt,
                            )
                        finally:
                            runner.cleanup_attempt(context.job.entity_id)
            except CodexAccountPoolBusy:
                self._mark_failure(context, "codex_account_pool_busy",
                                   "All eligible Codex worker profiles are busy.")
                return DeferredJobOutcome(
                    "codex_account_pool_busy", "All eligible Codex worker profiles are busy.",
                    datetime.now(timezone.utc) + timedelta(seconds=60),
                )
        if builtin_product and not local_product:
            try:
                verified_inpaint_result(stock.image_bytes, generated, "stock")
                final_png = finish_slot1_refinement(
                    slot7_material, generated.image_bytes, clip_raw_halo=True,
                    require_raw_stock=True, fine_thread_profile=True,
                )
            except (Slot1EmbroideryQAError, Stage2InpaintError) as exc:
                raise RrugcGenerationHandlerError(
                    "colorway_thread_inpaint_qa_failed", str(exc), retryable=False,
                ) from exc
            generated = GeneratedImageResult(
                provider="codex", model=generated.model, image_bytes=final_png,
                mime_type="image/png", provider_metadata={
                    **(generated.provider_metadata or {}),
                    "thread_material_remote_file_id": material_pin["material_remote_file_id"],
                    "stock_outside_thread_preserved": True,
                    "native_passes": 1,
                },
            )
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
            asset_id="rrugc-colorway:" + context.job.entity_id + ":" + getattr(context.job, "id", context.job.entity_id),
            content_type=generated.mime_type,
            size_bytes=len(generated.image_bytes),
            filename=f"output_{color_key}_{context.job.entity_id}_{getattr(context.job, 'id', context.job.entity_id)}{_output_extension(generated.mime_type)}",
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
            save_output_version(
                session, tenant_id=context.job.tenant_id, stage="stage2", job=row,
                remote_file_id=stored.remote_file_id, content_type=generated.mime_type,
                size_bytes=len(generated.image_bytes), width=width, height=height,
                processing_job_id=getattr(context.job, "id", None),
                font_info=({"thread_colorway_pipeline": {
                    "version": ("visor-accent-v1-raster" if with_visor_accent
                                else "threadtransfer-v2-colorway" if local_product
                                else "reference-reuse-v1"),
                    **({"visor_accent": visor_accent_meta} if visor_accent_meta else {}),
                    "native_passes": (generated.provider_metadata or {}).get("native_passes", 1),
                    "reference_sha256": (generated.provider_metadata or {}).get("reference_sha256"),
                    "thread_layer_sha256": (generated.provider_metadata or {}).get("thread_layer_sha256"),
                    "transfer_box": (generated.provider_metadata or {}).get("transfer_box"),
                    "stock_outside_thread_preserved": True,
                    "material_remote_file_id": material_pin["material_remote_file_id"],
                    "material_design_sha256": material_pin["material_design_sha256"],
                    "stock_sha256": row.stock_sha256,
                    "source_revision": row.source_revision,
                }} if builtin_product else None),
            )
            row.status = "completed"
            row.output_remote_file_id = stored.remote_file_id
            row.output_content_type = generated.mime_type
            row.output_size_bytes = len(generated.image_bytes)
            row.output_width, row.output_height = width, height
            row.completed_at = datetime.now(timezone.utc)
            row.last_error_code = row.last_error_message = None
            session.commit()
            finished_source_id = row.source_plan_id
            finished_revision = row.source_revision
        try:
            from app.modules.realistic_review_ugc.products_run_all import advance_after_output
            advance_after_output(
                context.dependencies.session_factory,
                tenant_id=context.job.tenant_id,
                source_plan_id=finished_source_id, source_revision=finished_revision,
                color_key=None,  # all active run-all selections for this design
            )
        except Exception:
            # Image remains completed; a periodic reconciler handles missed
            # scheduling signals without creating another provider request.
            context.logger.exception("products_run_all_colorway_handoff_failed",
                                     extra={"job_id": context.job.entity_id})
        context.logger.info("rrugc_colorway_completed",
                            extra={"job_id": context.job.entity_id, "color_key": color_key})
        return JobHandlerResult.completed()