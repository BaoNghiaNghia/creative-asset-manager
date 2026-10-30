"""Creative V3 direct GPT Image API generation (no Codex CLI tool dependency).

One explicitly authorized Stage 1 job generates six independently reviewed
concept masters. Each external request uses a single API call, no automatic
SDK retry, a pinned model, real PNG bytes, and the existing G5 technical gate.
No fake stitching, alpha-matting, auto Stage 2 handoff, or text OCR claims.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Any, Callable, Mapping

import openai
from openai import AsyncOpenAI

from app.modules.image_generation.providers import GeneratedImageFile, GeneratedImageResult
from app.modules.realistic_review_ugc.creative_v3_generation import (
    SLOT_COUNT, api_config_signature, enabled, image_provider,
    prompt_for_slot, verify_snapshot,
)
from app.modules.realistic_review_ugc.creative_v3_runner import (
    _checked_png, _fail, _job_directory,
)
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, _store_new, fit_native_on_stage1_canvas,
)

MAX_NATIVE_IMAGE_BYTES = 30 * 1024 * 1024


async def generate_six(
    *, context: Any, settings: Any,
    plan: Mapping[str, Any], snapshot: Mapping[str, Any],
    revalidate: Callable[[], Mapping[str, Any]],
) -> GeneratedImageResult:
    tenant_id = context.job.tenant_id
    if image_provider(settings) != "openai" or not enabled(settings, tenant_id):
        raise _fail("creative_v3_api_disabled", "OpenAI Image API is not enabled for this tenant.")
    if api_config_signature(settings) != snapshot.get("provider_sha256"):
        raise _fail("creative_v3_api_settings_changed", "Image model or quality changed after queueing.")
    verify_snapshot(plan, snapshot)
    if len(plan["slots"]) != SLOT_COUNT:
        raise _fail("creative_v3_slots_invalid", "Exactly six concept variants are required.")

    root = _job_directory(settings, tenant_id=tenant_id, processing_id=context.job.id)
    final_dir = root / "output" / "final"
    if root.is_symlink() or (root / "output").is_symlink() or final_dir.is_symlink():
        raise _fail("creative_v3_staging_unsafe", "Stage 1 staging directory is unsafe.")
    final_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    final_dir.chmod(0o700)
    api_key = str(getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        raise _fail("creative_v3_api_key_missing", "OpenAI API key is not configured.")
    model = str(settings.CREATIVE_ENGINE_V3_OPENAI_MODEL)
    quality = str(settings.CREATIVE_ENGINE_V3_OPENAI_QUALITY)
    timeout = max(60, min(600, int(settings.CREATIVE_ENGINE_V3_OPENAI_TIMEOUT_SECONDS)))
    # Avoid sending the raw tenant ID or keyword as a user identifier.
    safety_identifier = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:48]
    seen: set[str] = set()
    files: list[GeneratedImageFile] = []
    client = AsyncOpenAI(
        api_key=api_key,
        timeout=timeout,
        max_retries=0,
        organization=(getattr(settings, "OPENAI_ORGANIZATION", None) or None),
        project=(getattr(settings, "OPENAI_PROJECT", None) or None),
    )
    try:
        for slot in range(1, SLOT_COUNT + 1):
            if context.is_cancelled:
                raise _fail("creative_v3_cancelled", "Creative V3 job was cancelled.")
            if not enabled(settings, tenant_id) or api_config_signature(settings) != snapshot.get("provider_sha256"):
                raise _fail("creative_v3_revoked", "Image generation was disabled or reconfigured.")
            verify_snapshot(revalidate(), snapshot)
            target = final_dir / f"design_{slot:02}.png"
            if target.is_symlink():
                raise _fail("creative_v3_staging_unsafe", "A checkpoint is a symlink.")
            if target.is_file():
                digest, _qa = _checked_png(target.read_bytes(), seen)
                seen.add(digest)
            else:
                prompt = prompt_for_slot(plan, slot)
                if len(prompt) > 32_000:
                    raise _fail("creative_v3_prompt_too_long", "The V1 brief exceeds the image API prompt limit.")
                try:
                    response = await client.images.generate(
                        model=model,
                        prompt=prompt,
                        size="1536x1024",
                        quality=quality,
                        background="transparent",
                        output_format="png",
                        n=1,
                        extra_body={"safety_identifier": safety_identifier},
                        timeout=timeout,
                    )
                except openai.AuthenticationError as exc:
                    raise _fail("creative_v3_api_auth_failed", "OpenAI image API credentials were rejected.") from exc
                except openai.PermissionDeniedError as exc:
                    raise _fail("creative_v3_api_access_denied", "The OpenAI project cannot use this image model.") from exc
                except openai.RateLimitError as exc:
                    raise _fail("creative_v3_api_rate_limited", "OpenAI image API rate limit was reached.") from exc
                except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as exc:
                    raise _fail("creative_v3_api_transient", "OpenAI image API request did not finish.") from exc
                except openai.APIStatusError as exc:
                    raise _fail("creative_v3_api_rejected", "OpenAI image API rejected the request.") from exc

                if not response.data or len(response.data) != 1 or not response.data[0].b64_json:
                    raise _fail("creative_v3_api_no_image", "OpenAI image API returned no image bytes.")
                encoded = response.data[0].b64_json
                if len(encoded) > (MAX_NATIVE_IMAGE_BYTES * 4 // 3 + 1024):
                    raise _fail("creative_v3_api_image_too_large", "Image API response exceeds the native PNG limit.")
                try:
                    native = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise _fail("creative_v3_api_image_invalid", "Image API returned invalid encoded pixels.") from exc
                if not native or len(native) > MAX_NATIVE_IMAGE_BYTES:
                    raise _fail("creative_v3_api_image_invalid", "Image API returned an empty or oversized image.")
                native_sha = hashlib.sha256(native).hexdigest()
                try:
                    data, _padded = fit_native_on_stage1_canvas(native)
                except TypographyPilotError as exc:
                    raise _fail("creative_v3_api_alpha_invalid", "Image API did not return usable transparent PNG pixels.") from exc
                digest, qa = _checked_png(data, seen)
                verify_snapshot(revalidate(), snapshot)
                if (not enabled(settings, tenant_id) or context.is_cancelled
                        or api_config_signature(settings) != snapshot.get("provider_sha256")):
                    raise _fail("creative_v3_revoked", "Generation was revoked during the provider call.")
                try:
                    _store_new(target, data)
                except FileExistsError as exc:
                    raise _fail("creative_v3_slot_exists", "A slot checkpoint already exists.") from exc
                seen.add(digest)
                context.logger.info("rrugc_creative_v3_openai_slot_pass", extra={
                    "tenant_id": tenant_id, "job_id": context.job.id,
                    "slot": slot, "model": model, "native_sha256": native_sha[:12],
                    "technical_pass": qa["technical_pass"],
                    "image_spelling_verified": False,
                })
            files.append(GeneratedImageFile(
                path=str(target), filename=f"output/final/design_{slot:02}.png",
                mime_type="image/png",
            ))
    finally:
        await client.close()

    verify_snapshot(revalidate(), snapshot)
    if len(files) != SLOT_COUNT or len(seen) != SLOT_COUNT:
        raise _fail("creative_v3_six_outputs_required", "Six distinct transparent masters are required.")
    return GeneratedImageResult(
        provider="openai", model=model, image_bytes=b"",
        mime_type="image/png", output_files=tuple(files),
        provider_metadata={
            "provider": "openai_images_api", "model": model,
            "native_api_verified": True,
            "technical_qa": "passed",
            "image_spelling_verified": False, "embroidery_fit_verified": False,
            "creative_v3_fingerprint": plan["fingerprint"],
        },
    )
