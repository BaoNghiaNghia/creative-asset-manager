from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.database import (
    SessionLocal,
    validate_alembic_head,
    validate_database_connection,
)
from app.modules.processing.model import ProcessingJobModel


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
    args = parser.parse_args(argv)

    if args.apply and not args.yes:
        parser.error("--apply requires --yes")

    validate_database_connection()
    validate_alembic_head()
    result = requeue_download_stage_unconfigured(
        tenant_id=args.tenant_id,
        apply=args.apply,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
