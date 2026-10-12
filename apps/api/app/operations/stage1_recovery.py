"""Bounded Stage 1 recovery and post-publish Drive cleanup (VPS timer).

Existing published outputs always win. Automatic generation is scheduled only
from explicit retryable failures and never beyond the per-job budget. Missing
tools, invalid skills or corrupt checkpoints remain operator-attention items.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel,
)
from app.modules.realistic_review_ugc.keyword_images import (
    KeywordImageGenerateHandler, KeywordImageService,
    STAGE1_AUTO_RETRY_DELAYS_SECONDS, STAGE1_SAFE_ZERO_OUTPUT_CODES,
    STAGE1_PARTIAL_RECOVERY_CODES, STAGE1_RETRYABLE_STORAGE_CODES, DEFAULT_SKILL,
)
from app.providers.ai.codex_image import CodexImageGenRunner, load_codex_skill_manifest
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillRegistryError, resolve_stage2_skill, installed_stage2_skill_sha256,
)
from app.modules.realistic_review_ugc.skill_registry import ensure_skill_registry, assert_skill_enabled
from app.modules.storage.provider_factory import build_managed_storage_provider

LOGGER = logging.getLogger(__name__)
RECOVERABLE_CODES = (
    STAGE1_SAFE_ZERO_OUTPUT_CODES
    | STAGE1_PARTIAL_RECOVERY_CODES
    | STAGE1_RETRYABLE_STORAGE_CODES
    | frozenset({
        "stage1_previous_images_delete_failed",
        "stage2_skill_runtime_version_mismatch",
        "keyword_image_skill_changed",
    })
)
REPLACE_CODES = frozenset({
    "stage1_previous_images_delete_failed",
    "stage2_skill_runtime_version_mismatch",
    "keyword_image_skill_changed",
})


def refresh_job_skill(session, job: RrugcKeywordImageJobModel, settings) -> bool:
    """Re-pin a failed job to the current *same* enabled six-design Skill.

    Skill upgrades replace old versions. Never switch the job to a different
    artwork Skill, a disabled version or a non-six-design pipeline.
    """
    try:
        resolved = resolve_stage2_skill(
            settings=settings, skill_source=job.skill_source, skill_id=job.skill_id,
            skill_name=job.skill_name, skill_version=None,
            fallback_skill_name=DEFAULT_SKILL,
        )
        if resolved.skill_name != job.skill_name:
            return False
        ensure_skill_registry(session, tenant_id=job.tenant_id)
        assert_skill_enabled(
            session, tenant_id=job.tenant_id, source=resolved.source,
            skill_id=resolved.skill_id, skill_name=resolved.skill_name,
        )
        manifest = load_codex_skill_manifest(settings.CODEX_IMAGE_HOME, resolved.skill_name)
        if manifest is None or "keyword_six_designs" not in manifest.workflows:
            return False
        current_hash = installed_stage2_skill_sha256(resolved.skill_name, settings=settings)
        if not current_hash:
            return False
        job.skill_source = resolved.source
        job.skill_id = resolved.skill_id
        job.skill_version = resolved.skill_version
        job.skill_bundle_sha256 = current_hash
        return True
    except (Stage2SkillRegistryError, ValueError, OSError):
        return False


def recover_failed(*, limit: int, dry_run: bool = False) -> dict[str, int]:
    result = {"eligible": 0, "queued": 0, "upload_resumed": 0, "skipped": 0}
    settings = get_settings()
    with SessionLocal() as session:
        rows = list(session.scalars(select(RrugcKeywordImageJobModel).where(
            RrugcKeywordImageJobModel.status == "failed",
            RrugcKeywordImageJobModel.last_error_code.in_(RECOVERABLE_CODES),
            RrugcKeywordImageJobModel.retry_count < len(STAGE1_AUTO_RETRY_DELAYS_SECONDS),
        ).order_by(RrugcKeywordImageJobModel.updated_at.asc()).limit(max(1, limit * 12))))
    for candidate in rows:
        if result["queued"] + result["upload_resumed"] >= limit:
            break
        with SessionLocal() as session:
            job = session.scalar(select(RrugcKeywordImageJobModel).where(
                RrugcKeywordImageJobModel.id == candidate.id,
                RrugcKeywordImageJobModel.tenant_id == candidate.tenant_id,
                RrugcKeywordImageJobModel.status == "failed",
            ).with_for_update(skip_locked=True))
            if job is None or job.retry_count >= len(STAGE1_AUTO_RETRY_DELAYS_SECONDS):
                continue
            code = job.last_error_code or ""
            old_processing = session.get(ProcessingJobModel, job.processing_job_id) if job.processing_job_id else None
            if old_processing is not None and old_processing.status not in {"failed", "completed"}:
                result["skipped"] += 1
                continue
            service = KeywordImageService(session, settings=settings)
            if code in STAGE1_RETRYABLE_STORAGE_CODES:
                result["eligible"] += 1
                if dry_run:
                    continue
                try:
                    service.resume_upload(tenant_id=job.tenant_id, keyword_id=job.keyword_id)
                    job.retry_count = int(job.retry_count or 0) + 1
                    session.commit()
                    result["upload_resumed"] += 1
                except Exception:
                    result["skipped"] += 1
                    session.rollback()
                continue
            if job.output_remote_file_id and code not in REPLACE_CODES:
                result["skipped"] += 1
                continue
            if not refresh_job_skill(session, job, settings):
                result["skipped"] += 1
                session.rollback()
                continue
            if code in STAGE1_PARTIAL_RECOVERY_CODES:
                workspace = (Path(settings.IMAGE_GENERATION_STAGING_ROOT).resolve()
                             / "codex" / job.id)
                missing = CodexImageGenRunner.stage1_missing_final_numbers(workspace)
                if missing is None:
                    result["skipped"] += 1
                    continue
                # Historical jobs may have lost their old 2/6 files under the
                # previous cleanup policy. They can only regenerate from zero.
            result["eligible"] += 1
            if dry_run:
                continue
            job.retry_count = int(job.retry_count or 0) + 1
            job.status = "queued"
            job.last_error_code = None
            job.last_error_message = None
            job.completed_at = None
            service._enqueue(
                job, replace_outputs=code in REPLACE_CODES or bool(job.output_remote_file_id)
            )
            session.commit()
            result["queued"] += 1
    return result


async def cleanup_published(*, storage, limit: int) -> dict[str, int]:
    result = {"eligible": 0, "files_removed": 0, "errors": 0}
    with SessionLocal() as session:
        jobs = list(session.scalars(select(RrugcKeywordImageJobModel).where(
            RrugcKeywordImageJobModel.status == "completed",
            RrugcKeywordImageJobModel.output_remote_file_id.is_not(None),
        ).order_by(RrugcKeywordImageJobModel.updated_at.desc()).limit(500)))
        todo = []
        for job in jobs:
            active = session.scalar(select(RrugcImageOutputVersionModel).where(
                RrugcImageOutputVersionModel.tenant_id == job.tenant_id,
                RrugcImageOutputVersionModel.stage == "stage1",
                RrugcImageOutputVersionModel.job_id == job.id,
                RrugcImageOutputVersionModel.remote_file_id == job.output_remote_file_id,
            ))
            if active is None or not active.processing_job_id:
                continue
            # Only perform cleanup for completed explicit Regenerate jobs.
            processing = session.get(ProcessingJobModel, active.processing_job_id)
            if not processing or not (processing.payload_json or {}).get("replace_outputs"):
                continue
            stale = session.scalar(select(RrugcImageOutputVersionModel.id).where(
                RrugcImageOutputVersionModel.tenant_id == job.tenant_id,
                RrugcImageOutputVersionModel.stage == "stage1",
                RrugcImageOutputVersionModel.job_id == job.id,
                RrugcImageOutputVersionModel.id != active.id,
                RrugcImageOutputVersionModel.processing_job_id != active.processing_job_id,
            ).limit(1))
            if stale is None:
                stale = session.scalar(select(RrugcImageOutputVersionModel.id).where(
                    RrugcImageOutputVersionModel.tenant_id == job.tenant_id,
                    RrugcImageOutputVersionModel.stage == "stage1",
                    RrugcImageOutputVersionModel.job_id == job.id,
                    RrugcImageOutputVersionModel.processing_job_id.is_(None),
                ).limit(1))
            if stale is not None:
                todo.append((job.tenant_id, job.id, active.processing_job_id))
                if len(todo) >= limit:
                    break
    handler = KeywordImageGenerateHandler()
    for tenant_id, job_id, generation_id in todo:
        result["eligible"] += 1
        context = SimpleNamespace(
            job=SimpleNamespace(entity_id=job_id, tenant_id=tenant_id),
            dependencies=SimpleNamespace(session_factory=SessionLocal),
            logger=LOGGER,
        )
        try:
            result["files_removed"] += await handler._cleanup_replaced_stage1_outputs(
                context, storage, active_processing_id=generation_id,
            )
        except Exception as exc:
            result["errors"] += 1
            LOGGER.warning("stage1_cleanup_retry_pending job_id=%s error_type=%s",
                           job_id, type(exc).__name__)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if not 0 <= args.limit <= 20:
        parser.error("limit must be 0..20")
    if args.limit == 0:
        print(json.dumps({"eligible": 0, "queued": 0, "upload_resumed": 0}))
        return 0
    recovery = recover_failed(limit=args.limit, dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps({"mode": "dry-run", "recovery": recovery}))
        return 0
    storage = build_managed_storage_provider(get_settings())
    cleanup = asyncio.run(cleanup_published(storage=storage, limit=args.limit))
    print(json.dumps({"mode": "apply", "recovery": recovery, "cleanup": cleanup}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
