from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.database import (
    SessionLocal,
    validate_alembic_head,
    validate_database_connection,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository


DOWNLOAD_STAGE_ERROR = "download_stage_unconfigured"


def requeue_download_stage_unconfigured(
    *,
    tenant_id: str,
    apply: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, object]:
    criteria = (
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == "source_asset_download",
        ProcessingJobModel.status == "failed",
        ProcessingJobModel.last_error_code == DOWNLOAD_STAGE_ERROR,
    )
    with session_factory() as session:
        matched = int(
            session.scalar(
                select(func.count())
                .select_from(ProcessingJobModel)
                .where(*criteria)
            )
            or 0
        )
        if apply and matched:
            now = datetime.now(timezone.utc)
            session.execute(
                update(ProcessingJobModel)
                .where(*criteria)
                .values(
                    status="pending",
                    attempt_count=0,
                    next_attempt_at=now,
                    claimed_by=None,
                    claimed_at=None,
                    lease_expires_at=None,
                    last_error_code=None,
                    last_error_message=None,
                    completed_at=None,
                    concurrency_accounted=False,
                    updated_at=now,
                )
            )
            session.commit()
        else:
            session.rollback()
    return {
        "tenant_id": tenant_id,
        "matched": matched,
        "requeued": matched if apply else 0,
        "dry_run": not apply,
    }


def repair_downloads(
    *,
    tenant_id: str,
    apply: bool = False,
    limit: int | None = None,
    error_code: str | None = None,
    after_created_at: datetime | None = None,
    after_job_id: str | None = None,
    include_oversized: bool = False,
    verify: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, object]:
    if not tenant_id:
        raise ValueError("tenant_id is required")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if (after_created_at is None) != (after_job_id is None):
        raise ValueError("after_created_at and after_job_id must be provided together")

    newer = ProcessingJobModel.__table__.alias("newer_download_job")
    latest = ~select(1).where(
        newer.c.tenant_id == ProcessingJobModel.tenant_id,
        newer.c.job_type == ProcessingJobModel.job_type,
        newer.c.entity_type == ProcessingJobModel.entity_type,
        newer.c.entity_id == ProcessingJobModel.entity_id,
        or_(
            newer.c.created_at > ProcessingJobModel.created_at,
            and_(
                newer.c.created_at == ProcessingJobModel.created_at,
                newer.c.id > ProcessingJobModel.id,
            ),
        ),
    ).exists()
    criteria = [
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == "source_asset_download",
        ProcessingJobModel.entity_type == "source_asset",
        ProcessingJobModel.status == "failed",
        latest,
    ]
    if error_code:
        criteria.append(ProcessingJobModel.last_error_code == error_code)
    if after_created_at is not None and after_job_id is not None:
        criteria.append(or_(
            ProcessingJobModel.created_at > after_created_at,
            and_(
                ProcessingJobModel.created_at == after_created_at,
                ProcessingJobModel.id > after_job_id,
            ),
        ))

    with session_factory() as session:
        statement = select(ProcessingJobModel).where(*criteria).order_by(
            ProcessingJobModel.created_at, ProcessingJobModel.id
        )
        if limit is not None:
            statement = statement.limit(limit)
        candidates = list(session.scalars(statement))
        created = duplicate_skipped = skipped = failed = 0
        for failed_job in candidates:
            code = failed_job.last_error_code or ""
            message = failed_job.last_error_message or ""
            oversized = (
                code == "source_content_too_large"
                or "source content exceeds configured byte limit" in message
            )
            unsupported = code == "unsupported_source_mime_type"
            if (oversized and not include_oversized) or unsupported:
                skipped += 1
                continue
            key = f"repair:source_asset_download:{failed_job.id}:{failed_job.entity_id}"
            existing = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.idempotency_key == key,
            ))
            if existing is not None:
                duplicate_skipped += 1
                continue
            if not apply:
                continue
            try:
                ProcessingRepository(session).create_job(
                    tenant_id=tenant_id,
                    job_type="source_asset_download",
                    entity_type="source_asset",
                    entity_id=failed_job.entity_id,
                    idempotency_key=key,
                    payload={"source_asset_id": failed_job.entity_id},
                    provider_key=failed_job.provider_key,
                    provider_scope=failed_job.provider_scope,
                )
                created += 1
            except Exception:
                failed += 1
        cursor = candidates[-1] if candidates else None
        cursor_created_at = cursor.created_at if cursor else None
        cursor_job_id = cursor.id if cursor else None
        if apply:
            session.commit()
        else:
            session.rollback()
    return {
        "tenant_id": tenant_id,
        "scanned": len(candidates),
        "matched": len(candidates),
        "created": created,
        "repaired": created,
        "duplicate_jobs_skipped": duplicate_skipped,
        "skipped": skipped,
        "failed": failed,
        "dry_run": not apply,
        "verify": verify,
        "cursor": {
            "after_created_at": cursor_created_at.isoformat() if cursor_created_at else None,
            "after_job_id": cursor_job_id,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Processing job operations")
    commands = parser.add_subparsers(dest="command", required=True)
    requeue = commands.add_parser(
        "requeue-download-stage-unconfigured",
        help="Requeue failed source downloads after configuring the download stage",
    )
    requeue.add_argument("--tenant-id", required=True)
    requeue.add_argument("--apply", action="store_true")
    requeue.add_argument("--yes", action="store_true")
    repair = commands.add_parser(
        "pipeline:repair-downloads",
        help="Create fresh jobs for latest failed source downloads without mutating audit history",
    )
    repair.add_argument("--tenant-id", required=True)
    repair.add_argument("--apply", action="store_true")
    repair.add_argument("--yes", action="store_true")
    repair.add_argument("--limit", type=int)
    repair.add_argument("--error-code")
    repair.add_argument("--after-created-at")
    repair.add_argument("--after-job-id")
    repair.add_argument("--include-oversized", action="store_true")
    repair.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)

    if args.apply and not args.yes:
        parser.error("--apply requires --yes")

    validate_database_connection()
    validate_alembic_head()
    if args.command == "pipeline:repair-downloads":
        after_created_at = (
            datetime.fromisoformat(args.after_created_at)
            if args.after_created_at else None
        )
        result = repair_downloads(
            tenant_id=args.tenant_id,
            apply=args.apply,
            limit=args.limit,
            error_code=args.error_code,
            after_created_at=after_created_at,
            after_job_id=args.after_job_id,
            include_oversized=args.include_oversized,
            verify=args.verify,
        )
    else:
        result = requeue_download_stage_unconfigured(
            tenant_id=args.tenant_id,
            apply=args.apply,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
