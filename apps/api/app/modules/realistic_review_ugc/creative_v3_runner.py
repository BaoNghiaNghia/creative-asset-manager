"""Native ImageGen nine-slot adapter with resume support for legacy six-slot V3.

Reuses the existing Stage 1 worker and output upload/history pipeline. Each
slot is an independent native image with verified cache provenance; no
deterministic faux-embroidery fallback, automatic publishing to Stage 2 or OCR
claims. A partial batch remains private until all files pass G5 QA.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import shutil
from pathlib import Path
from uuid import UUID
from typing import Any, Callable, Mapping

from app.modules.image_generation.providers import GeneratedImageFile, GeneratedImageResult
from app.modules.realistic_review_ugc.codex_account_manager import (
    CodexAccountPoolBusy, acquire_worker_profile, mark_stage1_profile_cooldown,
)
from app.modules.realistic_review_ugc.creative_v3_generation import (
    CONTRACT, SKILL, SLOT_COUNT, LEGACY_SLOT_COUNT, LEGACY_VISUAL_PROFILE,
    CreativeV3Error, enabled, prompt_for_slot, verify_snapshot, VISUAL_PROFILE,
)
from app.modules.realistic_review_ugc.stage1_cap_art_direction import cap_fit_advisory
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, fit_native_on_stage1_canvas, _store_new,
)
from app.modules.realistic_review_ugc.typography_quality_gate import inspect_typography_png
from app.modules.realistic_review_ugc.stage1_visual_similarity import (
    stage1_visual_dhash, matches_rejected_visual,
)
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
    qa = dict(qa)
    # G5 has ALREADY decoded this PNG and computed a transparent-crop dHash.
    # Reuse it to avoid another expensive 2048x1024 decode on every slot.
    signature = qa.get("perceptual_hash")
    qa["stage1_visual_dhash"] = (
        "tdh1:" + signature if isinstance(signature, str) and len(signature) == 16
        and all(ch in "0123456789abcdef" for ch in signature)
        else stage1_visual_dhash(data)
    )
    return digest, qa


async def generate_six(
    *, context: Any, settings: Any,
    plan: Mapping[str, Any], snapshot: Mapping[str, Any],
    revalidate: Callable[[], Mapping[str, Any]],
) -> GeneratedImageResult:
    """Only a live nine- or legacy six-plan with pinned source may execute.

    The caller has already checked authorization, job type and the explicit
    server opt-in; we also check at every paid provider boundary.
    """
    tenant_id = context.job.tenant_id
    if not enabled(settings, tenant_id):
        raise _fail("creative_v3_disabled", "Creative Engine V3 ImageGen is not enabled.")
    verify_snapshot(plan, snapshot)
    count = len(plan["slots"])
    if count not in (LEGACY_SLOT_COUNT, SLOT_COUNT):
        raise _fail("creative_v3_slots_invalid", "Invalid Creative Brief V3 output count.")
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
    rejected_hashes = set((plan.get("learning_profile") or {}).get("rejected_hashes", []))
    rejected_visual_hashes = (plan.get("learning_profile") or {}).get("rejected_visual_hashes", [])
    outputs: list[GeneratedImageFile] = []
    cap_fit_reports: list[dict] = []
    heritage_reports: list[dict] = []

    # New nine-slot jobs can use two independently leased Codex accounts.
    # Older in-flight / historical jobs default to their pinned serial path.
    payload = getattr(context.job, "payload", {}) or {}
    requested_lanes = 2 if (count == SLOT_COUNT and
                            payload.get("creative_v3_parallel_slots") == 2) else 1
    remaining = list(range(1, count + 1))
    verified: dict[int, dict] = {}
    stop = asyncio.Event()
    actual_lanes = 1
    primary_home = str(getattr(settings, "CODEX_IMAGE_HOME", "/var/lib/creative-asset-manager/codex"))
    binary = str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex")

    async def _one_slot(slot: int, native_runner: CodexImageGenRunner) -> None:
        slot_started = time.monotonic()
        if context.is_cancelled:
            raise _fail("creative_v3_cancelled", "User cancelled Creative V3 generation.")
        if not enabled(settings, tenant_id):
            raise _fail("creative_v3_revoked", "Creative V3 generation was disabled.")
        verify_snapshot(revalidate(), snapshot)
        target = final_dir / f"design_{slot:02}.png"
        if target.is_symlink():
            raise _fail("creative_v3_staging_unsafe", "A Stage 1 checkpoint is a symlink.")
        checkpoint_reused = target.is_file()
        if checkpoint_reused:
            # Only a verified native checkpoint may be reused.
            data = target.read_bytes()
            digest, qa = _checked_png(data, seen)
            if digest in rejected_hashes or matches_rejected_visual(
                qa["stage1_visual_dhash"], rejected_visual_hashes,
            ):
                raise _fail("creative_v3_previously_rejected_image",
                            "Saved checkpoint matches an explicitly rejected Stage 1 design.")
        else:
            for qa_attempt in range(2):
                attempt_id = context.job.id + f"-v3-{slot:02}-qa{qa_attempt + 1}"
                try:
                    prompt = prompt_for_slot(
                        plan, slot,
                        visual_profile=snapshot.get("visual_profile") or LEGACY_VISUAL_PROFILE,
                        design_style=snapshot.get("design_style", "balanced"),
                        design_style_version=snapshot.get("design_style_version"),
                    )
                    if qa_attempt:
                        prompt += (
                            "\nTECHNICAL QA REPAIR: the previous PNG for THIS SLOT "
                            "failed validation. Create a new distinct image with "
                            "genuine transparency, safe alpha margins and crisp "
                            "legible typography; never reuse rejected pixels."
                        )
                    result = await native_runner.generate_from_references(
                        attempt_id=attempt_id, person=None, references=[], prompt=prompt,
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
                    if digest in rejected_hashes or matches_rejected_visual(
                        qa["stage1_visual_dhash"], rejected_visual_hashes,
                    ):
                        raise _fail(
                            "creative_v3_previously_rejected_image",
                            "Image is identical or visually very close to a rejected design. "
                            "Generate a substantially different arrangement.",
                        )
                    verify_snapshot(revalidate(), snapshot)
                    if not enabled(settings, tenant_id) or context.is_cancelled:
                        raise _fail("creative_v3_revoked", "Creative V3 generation was cancelled.")
                    try:
                        _store_new(target, data)
                    except FileExistsError as exc:
                        raise _fail("creative_v3_slot_exists",
                                    "Stage 1 slot already exists; will not overwrite it.") from exc
                    context.logger.info("rrugc_creative_v3_slot_qa", extra={
                        "tenant_id": tenant_id, "job_id": context.job.id,
                        "slot": slot, "technical_pass": qa["technical_pass"],
                        "image_spelling_verified": False,
                        "visual_material_verified": False,
                    })
                    break
                except CodexImageProviderError as exc:
                    if (qa_attempt == 0 and exc.code in {
                            "creative_v3_alpha_or_canvas_rejected",
                            "creative_v3_duplicate_image",
                            "creative_v3_previously_rejected_image",
                            "creative_v3_native_png_rejected",
                    }):
                        context.logger.warning("rrugc_creative_v3_slot_technical_retry", extra={
                            "tenant_id": tenant_id, "job_id": context.job.id,
                            "slot": slot, "error_code": exc.code,
                        })
                        continue
                    raise
                finally:
                    native_runner.cleanup_attempt(attempt_id)
        # No awaits between duplicate detection and commit: separate lanes
        # cannot publish the same native pixels as two distinct variants.
        seen.add(digest)
        verified[slot] = qa
        context.logger.info("rrugc_creative_v3_slot_completed", extra={
            "tenant_id": tenant_id, "job_id": context.job.id,
            "slot": slot, "duration_ms": round((time.monotonic() - slot_started) * 1000),
            "source": "checkpoint" if checkpoint_reused else "native",
        })

    async def _work_lane(native_runner: CodexImageGenRunner) -> None:
        while remaining and not stop.is_set():
            slot = remaining.pop(0)
            try:
                await _one_slot(slot, native_runner)
            except CodexImageProviderError as exc:
                if exc.code == "creative_v3_codex_cli_early_exit":
                    mark_stage1_profile_cooldown(native_runner.config.codex_home)
                # Do not start expensive new image calls after one lane fails.
                # The other in-flight native tool may still finish a checkpoint.
                stop.set()
                raise
            except Exception:
                stop.set()
                raise

    def _runner(home: str, *, lane: int) -> CodexImageGenRunner:
        return CodexImageGenRunner(CodexImageRunnerConfig(
            binary=binary, codex_home=home,
            staging_root=str(settings.IMAGE_GENERATION_STAGING_ROOT),
            skill_name=SKILL,
            timeout_seconds=max(60, min(1200, int(settings.CODEX_IMAGE_TIMEOUT_SECONDS))),
            model=str(settings.CODEX_IMAGE_MODEL).strip() or None,
            output_contract=CONTRACT,
            expected_output_count=1,
            # Separate bounded, non-sensitive logs. The primary lane keeps
            # the historic ID used by the Stage 1 Logs endpoint.
            execution_log_id=(context.job.id if lane == 1 else context.job.id + "-lane2"),
        ))

    with context.dependencies.session_factory() as session:
        with acquire_worker_profile(
            session, tenant_id, "stage1", primary_home, binary, SKILL,
        ) as (account_id, home):
            session.commit()
            context.logger.info("rrugc_creative_v3_native_dispatch", extra={
                "job_id": context.job.id, "tenant_id": tenant_id,
                "account_id": account_id, "slots": count, "requested_lanes": requested_lanes,
            })
            primary_runner = _runner(home, lane=1)
            if requested_lanes == 1:
                await _work_lane(primary_runner)
            else:
                async def _second_lane() -> None:
                    nonlocal actual_lanes
                    with context.dependencies.session_factory() as other_session:
                        try:
                            with acquire_worker_profile(
                                other_session, tenant_id, "stage1",
                                primary_home, binary, SKILL,
                            ) as (other_id, other_home):
                                other_session.commit()
                                # Account leasing, not the number of API/worker
                                # processes, controls total native concurrency.
                                actual_lanes = 2
                                context.logger.info("rrugc_creative_v3_parallel_lane", extra={
                                    "job_id": context.job.id,
                                    "tenant_id": tenant_id, "account_id": other_id,
                                    "lane": 2,
                                })
                                await _work_lane(_runner(other_home, lane=2))
                        except CodexAccountPoolBusy:
                            # Existing Stage 1/Stage 2 jobs have priority over
                            # the optional second lane; proceed on one profile.
                            context.logger.info("rrugc_creative_v3_parallel_capacity_busy", extra={
                                "job_id": context.job.id, "tenant_id": tenant_id,
                            })

                lane_results = await asyncio.gather(
                    _work_lane(primary_runner), _second_lane(),
                    return_exceptions=True,
                )
                errors = [item for item in lane_results if isinstance(item, BaseException)]
                if errors:
                    raise errors[0]

    for slot in range(1, count + 1):
        qa = verified.get(slot)
        if qa is None:
            raise _fail("creative_v3_outputs_required", "Stage 1 slot did not finish.")
        if snapshot.get("visual_profile") == VISUAL_PROFILE:
            cap_fit_reports.append({"slot": slot, **cap_fit_advisory(qa.get("alpha_bbox"))})
            if snapshot.get("design_style") == "heritage":
                from app.modules.realistic_review_ugc.heritage_lettering import heritage_fit_advisory
                heritage_reports.append({
                    "slot": slot,
                    **heritage_fit_advisory(
                        qa.get("alpha_bbox"), version=snapshot.get("design_style_version"),
                    ),
                })
        outputs.append(GeneratedImageFile(
            path=str(final_dir / f"design_{slot:02}.png"),
            filename=f"output/final/design_{slot:02}.png",
            mime_type="image/png",
        ))
    verify_snapshot(revalidate(), snapshot)
    if len(outputs) != count or len(seen) != count:
        raise _fail("creative_v3_outputs_required", f"All {count} distinct images are required.")
    return GeneratedImageResult(
        provider="codex", model=str(settings.CODEX_IMAGE_MODEL).strip() or None,
        image_bytes=b"", mime_type="image/png",
        output_files=tuple(outputs),
        provider_metadata={
            "creative_v3_fingerprint": plan["fingerprint"],
            "design_style": snapshot.get("design_style", "balanced"),
            "design_style_version": snapshot.get("design_style_version"),
            "style_policy_version": snapshot.get("style_policy_version"),
            "technical_qa": "passed",
            "image_spelling_verified": False,
            "embroidery_fit_verified": False,
            "visual_material_verified": False,
            "technical_qa_slot_retry_max": 1,
            "parallel_lanes_requested": requested_lanes,
            "parallel_lanes_used": actual_lanes,
            "cap_fit_advisory": cap_fit_reports,
            "heritage_fit_advisory": heritage_reports,
            "cap_fit_visual_verified": False,
        },
    )