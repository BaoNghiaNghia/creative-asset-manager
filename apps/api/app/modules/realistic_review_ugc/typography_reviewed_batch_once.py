"""Operator-only G6.3 private ImageGen batch inspection (read-only).

No ImageGen provider calls, asset uploads, output history writes or Stage 2
jobs. The default invocation is a dry-run without settings/DB access.
"""
from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.core.config import get_settings
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, enabled, resolve_plan,
)
from app.modules.realistic_review_ugc.typography_reviewed_batch import (
    inspect_reviewed_batch,
)


def _emit(status: str, **payload) -> None:
    # Never disclose the source phrase, private PNG paths or OAuth credentials.
    print(json.dumps({"status": status, **payload}, sort_keys=True))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only inspection of 1–6 human-reviewed Typography V2 pilot slots."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--keyword-id", required=True)
    parser.add_argument("--reference-id", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--execute", action="store_true", help="Explicit canary-gated private inspection")
    arguments = parser.parse_args(argv)
    if not arguments.execute:
        _emit("dry_run_only", imagegen_calls=0, published=False, stage2_jobs=0)
        return 0
    if not all(isinstance(value, str) and 1 <= len(value.strip()) <= 255 for value in (
        arguments.tenant_id, arguments.user_id,
        arguments.keyword_id, arguments.reference_id,
    )):
        _emit("identifiers_invalid", published=False)
        return 2
    try:
        uid = UUID(arguments.batch_id)
        if uid.version != 4 or str(uid) != arguments.batch_id:
            raise ValueError("not canonical UUID4")
    except (ValueError, AttributeError, TypeError):
        _emit("batch_invalid", published=False)
        return 2
    settings = get_settings()
    if not enabled(settings, arguments.tenant_id):
        _emit("pilot_disabled", published=False)
        return 2

    from app.core.database import SessionLocal

    def current_plan():
        with SessionLocal() as session:
            return resolve_plan(
                session,
                tenant_id=arguments.tenant_id,
                user_id=arguments.user_id,
                keyword_id=arguments.keyword_id,
                reference_id=arguments.reference_id,
            )

    try:
        manifest = inspect_reviewed_batch(
            settings=settings,
            tenant_id=arguments.tenant_id,
            batch_id=arguments.batch_id,
            plan=current_plan(),
            revalidate=current_plan,
        )
    except TypographyPilotError as exc:
        _emit(exc.code, published=False, stage2_jobs=0)
        return 2

    slots = manifest["slot_contract"]
    _emit(
        "private_batch_inspected",
        accepted_count=manifest["accepted_count"],
        accepted_slots=slots["eligible_slots"],
        failed_slots=slots["failed_slots"],
        pending_slots=slots["pending_slots"],
        needs_review_slots=slots["needs_review_slots"],
        partial=slots["partial"],
        completed=slots["completed"],
        published=False, stage2_jobs=0, output_history_writes=0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
