"""Single-slot, native ImageGen pilot for CAM Typography V2 (never auto-publishes).

Executed only by an explicit CLI operator behind three production flags plus a
separate ImageGen opt-in and tenant canary. One slot per invocation. Existing
Stage1/Stage2 job rows, output history, and user-facing Skill defaults are
never written here.
"""
from __future__ import annotations

import fcntl
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from PIL import Image, UnidentifiedImageError

from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel
from app.modules.realistic_review_ugc.typography_repository import TypographyReferenceRepository
from app.modules.realistic_review_ugc.typography_dna_review import effective_reviewed_dna, VERIFIED_RIGHTS
from app.modules.realistic_review_ugc.typography_preview import build_typography_previews
from app.modules.realistic_review_ugc.typography_generation_plan import (
    PLAN_VERSION, SKILL_NAME, build_concept_short_v2_plan,
)
from app.modules.realistic_review_ugc.typography_quality_gate import inspect_typography_png
from app.providers.ai.codex_image import CodexImageGenRunner

PILOT_SCHEMA = "typography-v2/pilot-slot-v1"
OUTPUT_CONTRACT = "typography_v2_single_slot"
ANALYZER_VERSION = "g3-layout-font-overlap-v1"


class TypographyPilotError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def enabled(settings: Any, tenant_id: str) -> bool:
    tenants = {
        item.strip() for item in str(getattr(settings, "TYPOGRAPHY_V2_CANARY_TENANT_IDS", "")).split(",")
        if item.strip()
    }
    return bool(
        getattr(settings, "TYPOGRAPHY_V2_ENABLED", False)
        and getattr(settings, "TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED", False)
        and getattr(settings, "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED", False)
        and getattr(settings, "IMAGE_GENERATION_ENABLED", False)
        and getattr(settings, "CODEX_IMAGE_GENERATION_ENABLED", False)
        and isinstance(tenant_id, str) and tenant_id in tenants
    )


def resolve_plan(
    session: Session, *, tenant_id: str, user_id: str,
    keyword_id: str, reference_id: str,
) -> dict:
    """Only tenant-scoped picked keywords and still-approved Style DNA."""
    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ))
    if keyword is None:
        raise TypographyPilotError("keyword_not_picked", "Selected Stage 0 keyword is unavailable.")
    repo = TypographyReferenceRepository(session)
    reference = repo.get(tenant_id, reference_id, user_id=user_id, manage=False)
    if (reference is None or reference.editorial_status != "approved"
            or reference.source_rights_status not in VERIFIED_RIGHTS):
        raise TypographyPilotError("reference_not_approved", "Rights-approved reference is unavailable.")
    dna = repo.dna_cached(
        tenant_id, reference.id, user_id=user_id, manage=False,
        analyzer_version=ANALYZER_VERSION, image_hash=reference.content_hash,
    )
    reviewed = effective_reviewed_dna(reference, dna) if dna is not None else None
    if reviewed is None:
        raise TypographyPilotError("dna_not_accepted", "Accepted and current Style DNA is unavailable.")
    previews = build_typography_previews(
        keyword.keyword, dna=reviewed, reference_id=reference.id, dna_id=dna.id, count=3,
    )
    return build_concept_short_v2_plan(
        keyword=keyword.keyword, reviewed_dna=reviewed,
        reference_id=reference.id, dna_id=dna.id, previews=previews,
    )


def fingerprint(plan: Mapping[str, Any]) -> str:
    if (plan.get("plan_version") != PLAN_VERSION or plan.get("skill") != SKILL_NAME
            or plan.get("ready_for_imagegen") is not False
            or plan.get("job_created") is not False
            or plan.get("preview_qa", {}).get("all_technical_pass") is not True):
        raise TypographyPilotError("plan_invalid", "V2 requires an approved, technically valid, read-only plan.")
    slots = plan.get("slots")
    if (not isinstance(slots, list) or len(slots) != 6
            or [item.get("slot") for item in slots if isinstance(item, dict)] != list(range(1, 7))):
        raise TypographyPilotError("plan_slots_invalid", "Exactly six distinct planned slot IDs are required.")
    return sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _storage_dir(settings: Any, tenant_id: str, batch_id: str) -> Path:
    try:
        uid = UUID(batch_id)
        if uid.version != 4 or str(uid) != batch_id:
            raise ValueError("Invalid batch ID")
    except (ValueError, AttributeError, TypeError) as exc:
        raise TypographyPilotError("batch_id_invalid", "Batch ID must be a canonical UUID4.") from exc
    staging = Path(settings.IMAGE_GENERATION_STAGING_ROOT).resolve()
    base = staging / "typography-v2-pilot"
    tenant_root = base / sha256(tenant_id.encode("utf-8")).hexdigest()[:20]
    path = tenant_root / batch_id
    for part in (base, tenant_root, path):
        if part.is_symlink() or (part.exists() and not part.is_dir()):
            raise TypographyPilotError("staging_unsafe", "Pilot storage path is unsafe.")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def fit_native_on_stage1_canvas(native: bytes) -> tuple[bytes, bool]:
    """Pad/crop transparent native pixels only, never upscale or redraw text.

    Real ImageGen commonly returns 1536x1024 rather than 2048x1024. A crop
    of just the nonempty alpha bbox can be centered unchanged on the output
    canvas. This operation MUST follow native provider hash verification.
    """
    try:
        with Image.open(BytesIO(native)) as opened:
            if (opened.format != "PNG" or opened.mode != "RGBA"
                    or opened.width > 4096 or opened.height > 4096
                    or opened.width * opened.height > 10_000_000):
                raise TypographyPilotError("native_png_unsupported", "Native ImageGen output is not a safe transparent PNG.")
            opened.load()
            alpha = opened.getchannel("A")
            bbox = alpha.getbbox()
            if bbox is None or any(alpha.getpixel(point) != 0 for point in (
                    (0, 0), (opened.width - 1, 0),
                    (0, opened.height - 1), (opened.width - 1, opened.height - 1))):
                raise TypographyPilotError("native_alpha_invalid", "ImageGen returned empty or non-transparent artwork.")
            if opened.size == (2048, 1024):
                return native, False
            crop = opened.crop(bbox)
            if not 1 <= crop.width <= 1950 or not 1 <= crop.height <= 962:
                raise TypographyPilotError("native_artwork_oversized", "Image cannot fit the Stage 1 embroidery canvas without scaling.")
            output = Image.new("RGBA", (2048, 1024), (0, 0, 0, 0))
            output.paste(crop, ((2048 - crop.width) // 2, (1024 - crop.height) // 2))
            stream = BytesIO()
            output.save(stream, format="PNG")
            return stream.getvalue(), True
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise TypographyPilotError("native_png_invalid", "ImageGen output is not a decodable PNG.") from exc


def _store_new(path: Path, contents: bytes) -> None:
    """Atomic no-overwrite persistence via an exclusive tempfile + hard link."""
    tmp = path.parent / (".new-" + uuid4().hex)
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp, path, follow_symlinks=False)
    finally:
        tmp.unlink(missing_ok=True)


async def generate_one_slot(
    *, settings: Any, tenant_id: str, batch_id: str, slot: int,
    plan: Mapping[str, Any], runner: CodexImageGenRunner,
    revalidate: Callable[[], dict],
) -> dict:
    """One native ImageGen call + G5 QA -> private, pending-review PNG.

    Revocation or a changed DNA hash during the model call prevents write.
    No hardcoded fallback or automatic retry; manual execution is bounded.
    """
    if not enabled(settings, tenant_id):
        raise TypographyPilotError("pilot_disabled", "ImageGen pilot is disabled.")
    if type(slot) is not int or not 1 <= slot <= 6:
        raise TypographyPilotError("slot_invalid", "Exactly one slot in 1-6 is required.")
    plan_sha = fingerprint(plan)
    spec = plan["slots"][slot - 1]
    if (spec.get("status") != "pending"
            or spec.get("source_keyword_exact") != plan.get("source_keyword_exact")
            or not isinstance(spec.get("prompt"), str) or not 1 <= len(spec["prompt"]) <= 10000):
        raise TypographyPilotError("slot_plan_invalid", "Slot instructions are invalid.")
    if (runner.config.skill_name != SKILL_NAME or runner.config.output_contract != OUTPUT_CONTRACT):
        raise TypographyPilotError("runner_invalid", "Wrong ImageGen Skill or contract.")
    directory = _storage_dir(settings, tenant_id, batch_id)
    image_file = directory / f"design_{slot:02}.png"
    audit_file = directory / f"slot_{slot:02}.json"
    # One global lock serializes pilot ImageGen across ALL tenants and batch
    # IDs on this worker. Keep one provider call in flight even if operators
    # launch different UUIDs concurrently; no paid retry stampede.
    fd = os.open(
        directory.parent.parent / ".imagegen-single-flight.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TypographyPilotError("imagegen_busy", "Another V2 ImageGen pilot is running.") from exc
        if image_file.exists() or audit_file.exists() or image_file.is_symlink() or audit_file.is_symlink():
            raise TypographyPilotError("slot_exists", "Pilot slot is already recorded; do not regenerate or overwrite.")
        attempt_id = "typography-v2-" + uuid4().hex
        try:
            result = await runner.generate_from_references(
                attempt_id=attempt_id, person=None, references=[], prompt=spec["prompt"],
            )
            native = result.image_bytes
            meta = result.provider_metadata or {}
            native_sha = sha256(native).hexdigest()
            if (meta.get("typography_v2_native_verified") is not True
                    or meta.get("native_sha256") != native_sha):
                raise TypographyPilotError("native_image_unverified", "Native ImageGen byte provenance is missing.")
            data, padded = fit_native_on_stage1_canvas(native)
            digest = sha256(data).hexdigest()
            qa = inspect_typography_png(data)
            if not qa["technical_pass"]:
                raise TypographyPilotError("g5_png_rejected", "Generated PNG failed G5 technical QA.")
            if not enabled(settings, tenant_id):
                raise TypographyPilotError("pilot_revoked", "Pilot was disabled during the provider call.")
            if fingerprint(revalidate()) != plan_sha:
                raise TypographyPilotError("source_changed", "Reference rights, DNA or keyword changed while generating.")
            for previous in directory.glob("design_??.png"):
                if previous.is_symlink() or not previous.is_file() or previous.stat().st_size > 25 * 1024 * 1024:
                    raise TypographyPilotError("staging_unsafe", "Previous pilot PNG is unsafe.")
                if sha256(previous.read_bytes()).hexdigest() == digest:
                    raise TypographyPilotError("duplicate_slot_image", "Two pilot slots cannot use identical image bytes.")
            record = {
                "schema_version": PILOT_SCHEMA, "slot": slot,
                "plan_sha256": plan_sha, "sha256": digest,
                "native_sha256": native_sha,
                "transparent_padding_only": padded,
                "technical_pass": True, "g5": qa,
                "native_imagegen_verified": True,
                "review": "pending", "image_spelling_verified": False,
                "eligible_for_publication": False,
                "stage1_output_saved": False,
            }
            _store_new(image_file, data)
            _store_new(audit_file, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
            return {
                "status": "needs_human_review", "slot": slot,
                "image_path": str(image_file), "audit_path": str(audit_file),
                "sha256": digest, "published": False,
            }
        finally:
            runner.cleanup_attempt(attempt_id)
    finally:
        os.close(fd)
