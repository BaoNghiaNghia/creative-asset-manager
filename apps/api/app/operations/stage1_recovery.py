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

from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel,
)
from app.modules.realistic_review_ugc.keyword_images import (
    KeywordImageError, KeywordImageGenerateHandler, KeywordImageService,
    STAGE1_AUTO_RETRY_DELAYS_SECONDS, STAGE1_SAFE_ZERO_OUTPUT_CODES,
    STAGE1_PARTIAL_RECOVERY_CODES, STAGE1_RETRYABLE_STORAGE_CODES, DEFAULT_SKILL,
)
from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageProviderError, load_codex_skill_manifest
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
EXPECTED_FINAL_NAMES = frozenset(
    f"output/final/design_{n:02}.png" for n in range(1, 7)
)


def audit_stage1() -> dict:
    """Read-only, aggregate production health; never expose tenant or quote text.

    Only the six numbered PNG versions matching the *published first file* and
    its generation attempt count as a verified set. Historic versions are
    never used to inflate the active generation's output count.
    """
    with SessionLocal() as session:
        status_counts = {
            str(status): int(total)
            for status, total in session.execute(
                select(RrugcKeywordImageJobModel.status, func.count())
                .group_by(RrugcKeywordImageJobModel.status)
            )
        }
        failed_codes = {
            str(code or "unknown"): int(total)
            for code, total in session.execute(
                select(RrugcKeywordImageJobModel.last_error_code, func.count())
                .where(RrugcKeywordImageJobModel.status == "failed")
                .group_by(RrugcKeywordImageJobModel.last_error_code)
            )
        }
        active = aliased(RrugcImageOutputVersionModel)
        sibling = aliased(RrugcImageOutputVersionModel)
        verified = int(session.scalar(
            select(func.count()).select_from(
                select(RrugcKeywordImageJobModel.id)
                .join(active, and_(
                    active.tenant_id == RrugcKeywordImageJobModel.tenant_id,
                    active.stage == "stage1",
                    active.job_id == RrugcKeywordImageJobModel.id,
                    active.remote_file_id == RrugcKeywordImageJobModel.output_remote_file_id,
                    active.processing_job_id.is_not(None),
                ))
                .join(sibling, and_(
                    sibling.tenant_id == active.tenant_id,
                    sibling.stage == "stage1",
                    sibling.job_id == active.job_id,
                    sibling.processing_job_id == active.processing_job_id,
                    sibling.output_name.in_(EXPECTED_FINAL_NAMES),
                    sibling.content_type == "image/png",
                ))
                .where(RrugcKeywordImageJobModel.status == "completed")
                .group_by(RrugcKeywordImageJobModel.id)
                .having(func.count(func.distinct(sibling.output_name)) == 6)
                .subquery()
            )
        ) or 0)
        completed = status_counts.get("completed", 0)
        exhausted = int(session.scalar(
            select(func.count()).select_from(RrugcKeywordImageJobModel)
            .where(
                RrugcKeywordImageJobModel.status == "failed",
                RrugcKeywordImageJobModel.retry_count >= len(STAGE1_AUTO_RETRY_DELAYS_SECONDS),
            )
        ) or 0)
    return {
        "statuses": status_counts,
        "completed_verified_six": verified,
        "completed_unverified": max(0, completed - verified),
        "failed_codes": failed_codes,
        "failed_retry_budget_exhausted": exhausted,
    }


def resumable_or_empty_stage1_workspace(workspace: Path) -> bool:
    """Allow generation fallback only when checkpoints are safe or truly gone.

    One to five canonical valid PNGs can be resumed without modifying them.
    Zero canonical PNGs can start fresh ONLY when no other raster candidate
    remains in any output directory. Ambiguous or symlinked artifacts are
    operator-attention items, never silently discarded.
    """
    if workspace.is_symlink():
        return False
    missing = CodexImageGenRunner.stage1_missing_final_numbers(workspace)
    if missing is None or not missing:
        return False
    if len(missing) < 6:
        return True
    for name in ("output", "generated", "results"):
        directory = workspace / name
        if directory.is_symlink():
            return False
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_symlink():
                return False
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                return False
    return True


def requeue_missing_upload_artwork(
    *, tenant_id: str, job_id: str, settings,
) -> bool:
    """Bounded copy-on-write restart after a permanent loss of local finals."""
    with SessionLocal() as session:
        job = session.scalar(
            select(RrugcKeywordImageJobModel).where(
                RrugcKeywordImageJobModel.id == job_id,
                RrugcKeywordImageJobModel.tenant_id == tenant_id,
                RrugcKeywordImageJobModel.status == "failed",
                RrugcKeywordImageJobModel.last_error_code.in_(STAGE1_RETRYABLE_STORAGE_CODES),
            ).with_for_update(skip_locked=True)
        )
        if job is None or int(job.retry_count or 0) >= len(STAGE1_AUTO_RETRY_DELAYS_SECONDS):
            return False
        old = session.get(ProcessingJobModel, job.processing_job_id) if job.processing_job_id else None
        if old is not None and old.status not in {"failed", "completed"}:
            return False
        workspace = (Path(settings.IMAGE_GENERATION_STAGING_ROOT).resolve() / "codex" / job.id)
        if not resumable_or_empty_stage1_workspace(workspace):
            return False
        if not refresh_job_skill(session, job, settings):
            return False
        job.retry_count = int(job.retry_count or 0) + 1
        job.status = "queued"
        job.last_error_code = None
        job.last_error_message = None
        job.completed_at = None
        # Publish a complete new six-file attempt before any previously
        # uploaded Drive versions can be retired.
        KeywordImageService(session, settings=settings)._enqueue(
            job, replace_outputs=bool(job.output_remote_file_id)
        )
        session.commit()
        LOGGER.warning("stage1_missing_upload_artwork_requeued job_id=%s retry=%d",
                       job.id, job.retry_count)
        return True


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
                except KeywordImageError as exc:
                    result["skipped"] += 1
                    session.rollback()
                    # Only report a stable code. Do not log untrusted provider
                    # text, quote text or secrets from the underlying exception.
                    LOGGER.warning(
                        "stage1_upload_recovery_skipped job_id=%s code=%s",
                        job.id, exc.code,
                    )
                    if exc.code == "keyword_image_saved_finals_missing":
                        try:
                            if requeue_missing_upload_artwork(
                                tenant_id=job.tenant_id, job_id=job.id, settings=settings,
                            ):
                                result["skipped"] -= 1
                                result["queued"] += 1
                        except Exception as recovery_exc:
                            LOGGER.warning(
                                "stage1_missing_upload_artwork_requeue_failed job_id=%s error_type=%s",
                                job.id, type(recovery_exc).__name__,
                            )
                except Exception as exc:
                    result["skipped"] += 1
                    session.rollback()
                    LOGGER.warning(
                        "stage1_upload_recovery_unexpected job_id=%s error_type=%s",
                        job.id, type(exc).__name__,
                    )
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
                if code == "stage1_six_outputs_invalid" and not missing:
                    # Six numbered files can coexist with an unexpected
                    # additional final PNG. Do not queue a repeat that cannot
                    # fix the structure; only a verifiable set may upload.
                    try:
                        CodexImageGenRunner._collect_six_final_designs(workspace)
                    except CodexImageProviderError:
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
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if not 0 <= args.limit <= 20:
        parser.error("limit must be 0..20")
    if args.audit_only:
        print(json.dumps({"mode": "audit", "stage1": audit_stage1()}))
        return 0
    if args.limit == 0:
        print(json.dumps({"mode": "no-op", "stage1": audit_stage1()}))
        return 0
    recovery = recover_failed(limit=args.limit, dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps({"mode": "dry-run", "recovery": recovery, "stage1": audit_stage1()}))
        return 0
    storage = build_managed_storage_provider(get_settings())
    cleanup = asyncio.run(cleanup_published(storage=storage, limit=args.limit))
    print(json.dumps({"mode": "apply", "recovery": recovery, "cleanup": cleanup, "stage1": audit_stage1()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
