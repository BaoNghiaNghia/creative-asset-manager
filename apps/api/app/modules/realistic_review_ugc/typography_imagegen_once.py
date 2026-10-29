"""Operator-only G6 ImageGen pilot: one slot; never auto-start or publish.

Example, only AFTER G0.1/rollback is independently verified and all pilot
flags/canary are explicitly enabled:
python -m app.modules.realistic_review_ugc.typography_imagegen_once \
  --tenant-id TENANT --user-id OWNER --keyword-id KEYWORD --reference-id REF \
  --batch-id 01234567-89ab-4def-8123-456789abcdef --slot 1 --execute

Without --execute: no database, provider, storage or job construction.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from app.core.config import get_settings
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    OUTPUT_CONTRACT, enabled, resolve_plan, generate_one_slot,
    TypographyPilotError,
)
from app.modules.realistic_review_ugc.typography_generation_plan import SKILL_NAME
from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError,
    load_codex_skill_manifest,
)


def _emit(status: str, **extra) -> None:
    """Never print literal keywords, reference images, credentials or prompts."""
    print(json.dumps({"status": status, **extra}, sort_keys=True))


async def _run(args, settings):
    # Recheck right before DB and any model construction; no implicit fallback.
    if not enabled(settings, args.tenant_id):
        raise TypographyPilotError("pilot_disabled", "Feature, provider or rollback gate is disabled.")
    # The pilot's Skill uses a distinct workflow, NOT keyword_six_designs:
    # it must not appear as a selectable v1 Stage 1 Skill.
    manifest = load_codex_skill_manifest(settings.CODEX_IMAGE_HOME, SKILL_NAME)
    if (manifest is None or manifest.skill_name != SKILL_NAME or
            "typography_v2_single_slot" not in manifest.workflows):
        raise TypographyPilotError("v2_skill_missing", "Separate V2 single-slot Skill is not installed.")
    from app.core.database import SessionLocal

    def current_plan():
        with SessionLocal() as session:
            return resolve_plan(
                session, tenant_id=args.tenant_id, user_id=args.user_id,
                keyword_id=args.keyword_id, reference_id=args.reference_id,
            )

    plan = current_plan()
    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        binary=settings.CODEX_IMAGE_BINARY,
        codex_home=settings.CODEX_IMAGE_HOME,
        staging_root=settings.IMAGE_GENERATION_STAGING_ROOT,
        skill_name=SKILL_NAME,
        timeout_seconds=max(60, min(1200, int(settings.CODEX_IMAGE_TIMEOUT_SECONDS))),
        model=str(settings.CODEX_IMAGE_MODEL).strip() or None,
        output_contract=OUTPUT_CONTRACT,
        expected_output_count=1,
    ))
    if runner.capability_reason():
        raise TypographyPilotError("native_imagegen_unavailable", "Codex ImageGen runner is unavailable.")
    return await generate_one_slot(
        settings=settings, tenant_id=args.tenant_id, batch_id=args.batch_id,
        slot=args.slot, plan=plan, runner=runner, revalidate=current_plan,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One gated Typography V2 real ImageGen slot; disabled by default.")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--keyword-id", required=True)
    parser.add_argument("--reference-id", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        _emit("dry_run_only", provider_calls=0)
        return 0
    # Preflight before loading DB or constructing runner: fail-closed.
    if not all(isinstance(s, str) and 0 < len(s) <= 255 and s.strip()
               for s in (args.tenant_id, args.user_id, args.keyword_id, args.reference_id)):
        _emit("invalid_identifiers", provider_calls=0)
        return 2
    try:
        uuid = UUID(args.batch_id)
        if uuid.version != 4 or str(uuid) != args.batch_id or not 1 <= args.slot <= 6:
            raise ValueError("invalid batch or slot")
    except (ValueError, TypeError, AttributeError):
        _emit("invalid_slot_or_batch", provider_calls=0)
        return 2
    settings = get_settings()
    if not enabled(settings, args.tenant_id):
        _emit("pilot_disabled", provider_calls=0)
        return 0
    try:
        outcome = asyncio.run(_run(args, settings))
        _emit(outcome["status"], slot=args.slot,
              published=False, image_path=outcome["image_path"],
              audit_path=outcome["audit_path"], provider_calls=1)
        return 0
    except (TypographyPilotError, CodexImageProviderError) as exc:
        _emit(getattr(exc, "code", "imagegen_error"), provider_calls="0_or_1", published=False)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
