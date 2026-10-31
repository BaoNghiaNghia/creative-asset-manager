"""Native ImageGen six-slot adapter for explicitly queued Creative Brief V3.

Reuses the existing Stage 1 worker and output upload/history pipeline. Each
slot is an independent native image with verified cache provenance; no
deterministic faux-embroidery fallback, automatic publishing to Stage 2 or OCR
claims. A partial six-slot batch remains private until all files pass G5 QA.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import UUID
from typing import Any, Callable, Mapping

from app.modules.image_generation.providers import GeneratedImageFile, GeneratedImageResult
from app.modules.realistic_review_ugc.codex_account_manager import acquire_worker_profile
from app.modules.realistic_review_ugc.creative_v3_generation import (
    CONTRACT, SKILL, SLOT_COUNT, LEGACY_VISUAL_PROFILE,
    CreativeV3Error, enabled, prompt_for_slot, verify_snapshot,
)
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, fit_native_on_stage1_canvas, _store_new,
)
from app.modules.realistic_review_ugc.typography_quality_gate import inspect_typography_png
from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageProviderError, CodexImageRunnerConfig,
)


def _fail(code: str, message: str) -> CodexImageProviderError:
    return CodexImageProviderError(code, message, retryable=False)


def _job_directory(settings: Any, *, tenant_id: str, processing_id: str) -> Path:
    try:
        uid = UUID(processing_id)
        if str(uid) != processing_id:
            raise ValueError("Non-canonical UUID")
    except (TypeError, ValueError, AttributeError) as exc:
        raise _fail("creative_v3_job_id_invalid", "Invalid processing job identifier.") from exc
    parent = Path(settings.IMAGE_GENERATION_STAGING_ROOT).resolve() / "creative-v3"
    tenant_root = parent / hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:20]
    directory = tenant_root / processing_id
    for path in (parent, tenant_root, directory):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise _fail("creative_v3_staging_unsafe", "Creative V3 staging directory is unsafe.")
    return directory


def cleanup_job(*, settings: Any, tenant_id: str, processing_id: str) -> None:
    """Remove only this V3 processing attempt's disposable native checkpoints."""
    directory = _job_directory(settings, tenant_id=tenant_id, processing_id=processing_id)
    if directory.is_dir():
        shutil.rmtree(directory)


def _checked_png(data: bytes, previous: set[str]) -> tuple[str, dict]:
    qa = inspect_typography_png(data)
    if qa.get("technical_pass") is not True:
        raise _fail("creative_v3_alpha_or_canvas_rejected",
                    "Native V3 image did not pass transparent 2048x1024 embroidery PNG QA.")
    digest = hashlib.sha256(data).hexdigest()
    if digest in previous:
        raise _fail("creative_v3_duplicate_image", "Two V3 concepts generated identical image pixels.")
    return digest, qa


async def generate_six(
    *, context: Any, settings: Any,
    plan: Mapping[str, Any], snapshot: Mapping[str, Any],
    revalidate: Callable[[], Mapping[str, Any]],
) -> GeneratedImageResult:
    """Only the live six-plan with the pinned source snapshot may execute.

    The caller has already checked authorization, job type and the explicit
    server opt-in; we also check at every paid provider boundary.
    """
    tenant_id = context.job.tenant_id
    if not enabled(settings, tenant_id):
        raise _fail("creative_v3_disabled", "Creative Engine V3 ImageGen is not enabled.")
    verify_snapshot(plan, snapshot)
    if len(plan["slots"]) != SLOT_COUNT:
        raise _fail("creative_v3_slots_invalid", "Expected six Creative Brief V1 variants.")
    # Processing job ID isolates new V3 attempts from the keyword's existing
    # legacy Stage 1 checkpoints. Never silently relabel old images as V3.
    # Each ProcessingJob ID has an isolated workspace. Regeneration reuses
    # the logical Stage 1 row, but never reuses an earlier processing checkpoint.
    root = _job_directory(settings, tenant_id=tenant_id, processing_id=context.job.id)
    final_dir = root / "output" / "final"
    if root.is_symlink() or (root / "output").is_symlink() or final_dir.is_symlink():
        raise _fail("creative_v3_staging_unsafe", "Creative V3 output workspace is unsafe.")
    final_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    final_dir.chmod(0o700)
    seen: set[str] = set()
    outputs: list[GeneratedImageFile] = []

    with context.dependencies.session_factory() as session:
        primary_home = str(getattr(settings, "CODEX_IMAGE_HOME", "/var/lib/creative-asset-manager/codex"))
        binary = str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex")
        with acquire_worker_profile(session, tenant_id, "stage1", primary_home, binary, SKILL) as (account_id, home):
            session.commit()
            context.logger.info("rrugc_creative_v3_native_dispatch", extra={
                "job_id": context.job.id, "tenant_id": tenant_id, "account_id": account_id,
                "slots": SLOT_COUNT,
            })
            native_runner = CodexImageGenRunner(CodexImageRunnerConfig(
                binary=binary, codex_home=home,
                staging_root=str(settings.IMAGE_GENERATION_STAGING_ROOT),
                skill_name=SKILL,
                timeout_seconds=max(60, min(1200, int(settings.CODEX_IMAGE_TIMEOUT_SECONDS))),
                model=str(settings.CODEX_IMAGE_MODEL).strip() or None,
                output_contract=CONTRACT,
                expected_output_count=1,
                execution_log_id=context.job.id,
            ))
            for slot in range(1, SLOT_COUNT + 1):
                if context.is_cancelled:
                    raise _fail("creative_v3_cancelled", "User cancelled Creative V3 generation.")
                if not enabled(settings, tenant_id):
                    raise _fail("creative_v3_revoked", "Creative V3 generation was disabled.")
                verify_snapshot(revalidate(), snapshot)
                target = final_dir / f"design_{slot:02}.png"
                if target.is_symlink():
                    raise _fail("creative_v3_staging_unsafe", "A Stage 1 checkpoint is a symlink.")
                if target.is_file():
                    # An interrupted worker may resume only a verifiably
                    # complete, unique, valid PNG checkpoint.
                    data = target.read_bytes()
                    digest, _ = _checked_png(data, seen)
                    seen.add(digest)
                else:
                    attempt_id = context.job.id + f"-v3-{slot:02}"
                    try:
                        result = await native_runner.generate_from_references(
                            attempt_id=attempt_id, person=None, references=[],
                            prompt=prompt_for_slot(
                                plan, slot,
                                visual_profile=snapshot.get("visual_profile") or LEGACY_VISUAL_PROFILE,
                            ),
                        )
                        native = result.image_bytes
                        native_hash = hashlib.sha256(native).hexdigest()
                        meta = result.provider_metadata or {}
                        if (meta.get("typography_v2_native_verified") is not True
                                or meta.get("native_sha256") != native_hash):
                            raise _fail("creative_v3_native_unverified",
                                        "ImageGen native output cannot be verified by SHA-256.")
                        try:
                            data, _padded_only = fit_native_on_stage1_canvas(native)
                        except TypographyPilotError as exc:
                            raise _fail("creative_v3_native_png_rejected",
                                        "Native ImageGen returned a PNG without safe transparent margins.") from exc
                        digest, qa = _checked_png(data, seen)
                        # A brief source edit, rights revocation or feature
                        # disable during provider execution must block writes.
                        verify_snapshot(revalidate(), snapshot)
                        if not enabled(settings, tenant_id) or context.is_cancelled:
                            raise _fail("creative_v3_revoked", "Creative V3 generation was cancelled.")
                        try:
                            _store_new(target, data)
                        except FileExistsError as exc:
                            raise _fail("creative_v3_slot_exists",
                                        "Stage 1 slot already exists; will not overwrite it.") from exc
                        seen.add(digest)
                        context.logger.info("rrugc_creative_v3_slot_qa", extra={
                            "tenant_id": tenant_id, "job_id": context.job.id,
                            "slot": slot, "technical_pass": qa["technical_pass"],
                            "image_spelling_verified": False,
                        })
                    finally:
                        native_runner.cleanup_attempt(attempt_id)
                outputs.append(GeneratedImageFile(
                    path=str(target),
                    filename=f"output/final/design_{slot:02}.png",
                    mime_type="image/png",
                ))
    verify_snapshot(revalidate(), snapshot)
    if len(outputs) != SLOT_COUNT or len(seen) != SLOT_COUNT:
        raise _fail("creative_v3_six_outputs_required", "All six distinct images are required.")
    return GeneratedImageResult(
        provider="codex", model=str(settings.CODEX_IMAGE_MODEL).strip() or None,
        image_bytes=b"", mime_type="image/png",
        output_files=tuple(outputs),
        provider_metadata={
            "creative_v3_fingerprint": plan["fingerprint"],
            "technical_qa": "passed",
            "image_spelling_verified": False,
            "embroidery_fit_verified": False,
        },
    )
