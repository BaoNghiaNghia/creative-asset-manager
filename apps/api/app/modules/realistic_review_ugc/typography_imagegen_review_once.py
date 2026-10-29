"""Manual G6.2 ImageGen review CLI (never automatically publishes any artwork).

Invoke inspect first, visually open the real PNG, then explicitly record one
decision with its SHA-256. This command is operator-only, not a web endpoint.
"""
from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.core.config import get_settings
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, enabled, resolve_plan,
)
from app.modules.realistic_review_ugc.typography_imagegen_review import (
    REJECT_REASONS, inspect_one_slot, review_one_slot,
)


def _emit(status: str, **details) -> None:
    # No keyword, user-supplied reason text, prompts, URLs, or provider secrets.
    print(json.dumps({"status": status, **details}, sort_keys=True))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Private human review of one Typography V2 ImageGen pilot PNG.")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True, help="Reviewer identity with reference visibility")
    parser.add_argument("--keyword-id", required=True)
    parser.add_argument("--reference-id", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--decision", choices=["inspect", "accepted", "rejected"], default="inspect")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--image-spelling-verified", action="store_true")
    parser.add_argument("--originality-verified", action="store_true")
    parser.add_argument("--embroidery-verified", action="store_true")
    parser.add_argument("--reason-code", choices=sorted(REJECT_REASONS))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        _emit("dry_run_only", review_records=0, published=False)
        return 0
    if not all(isinstance(value, str) and 1 <= len(value.strip()) <= 255 for value in (
        args.tenant_id, args.user_id, args.keyword_id, args.reference_id,
    )):
        _emit("identifiers_invalid", review_records=0, published=False)
        return 2
    try:
        batch_uuid = UUID(args.batch_id)
        if batch_uuid.version != 4 or str(batch_uuid) != args.batch_id or not 1 <= args.slot <= 6:
            raise ValueError("invalid batch or slot")
    except (ValueError, TypeError, AttributeError):
        _emit("slot_or_batch_invalid", review_records=0, published=False)
        return 2
    settings = get_settings()
    if not enabled(settings, args.tenant_id):
        _emit("pilot_disabled", review_records=0, published=False)
        return 2
    from app.core.database import SessionLocal

    def current_plan():
        with SessionLocal() as session:
            return resolve_plan(
                session, tenant_id=args.tenant_id, user_id=args.user_id,
                keyword_id=args.keyword_id, reference_id=args.reference_id,
            )

    try:
        plan = current_plan()
        if args.decision == "inspect":
            if (args.expected_sha256 or args.image_spelling_verified
                    or args.originality_verified or args.embroidery_verified or args.reason_code):
                raise TypographyPilotError("inspect_flags_invalid", "Inspect must not include review decisions.")
            result = inspect_one_slot(
                settings=settings, tenant_id=args.tenant_id, batch_id=args.batch_id,
                slot=args.slot, plan=plan,
            )
            _emit("inspection_required", slot=args.slot, sha256=result["sha256"],
                  image_path=result["image_path"], review="pending", published=False)
            return 0

        result = review_one_slot(
            settings=settings, tenant_id=args.tenant_id, batch_id=args.batch_id,
            slot=args.slot, plan=plan, reviewer_id=args.user_id,
            decision=args.decision, expected_sha256=args.expected_sha256,
            image_spelling_verified=args.image_spelling_verified,
            originality_verified=args.originality_verified,
            embroidery_verified=args.embroidery_verified,
            reason_code=args.reason_code,
            revalidate=current_plan,
        )
        _emit(result["status"], slot=args.slot, decision=args.decision,
              sha256=result["sha256"], published=False, review_records=1)
        return 0
    except TypographyPilotError as exc:
        _emit(exc.code, review_records=0, published=False)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
