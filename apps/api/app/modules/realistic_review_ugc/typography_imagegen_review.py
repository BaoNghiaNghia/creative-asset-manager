"""Operator-only, immutable G6.2 review evidence for a native ImageGen pilot slot.

This is a private review checkpoint. It never publishes to Stage 1 / Stage 2
and never turns a filesystem review into an authorization to publish.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping
from uuid import UUID

from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    PILOT_SCHEMA, TypographyPilotError, _store_new, enabled, fingerprint,
)
from app.modules.realistic_review_ugc.typography_quality_gate import inspect_typography_png

REVIEW_SCHEMA = "typography-v2/pilot-review-v1"
REJECT_REASONS = frozenset({
    "spelling", "originality", "embroidery", "visual_quality", "rights",
    "source_changed", "other",
})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PNG = 25 * 1024 * 1024
_MAX_MANIFEST = 32 * 1024


def _existing_batch_dir(settings: Any, tenant_id: str, batch_id: str) -> Path:
    """Review must not create a tenant, batch, or provider staging directory."""
    try:
        value = UUID(batch_id)
        if value.version != 4 or str(value) != batch_id:
            raise ValueError("not a canonical UUID4")
    except (ValueError, TypeError, AttributeError) as exc:
        raise TypographyPilotError("batch_id_invalid", "Batch must be a canonical UUID4.") from exc
    if not tenant_id or not isinstance(tenant_id, str):
        raise TypographyPilotError("tenant_invalid", "A tenant is required.")
    staging = Path(settings.IMAGE_GENERATION_STAGING_ROOT).resolve()
    base = staging / "typography-v2-pilot"
    tenant_dir = base / sha256(tenant_id.encode("utf-8")).hexdigest()[:20]
    directory = tenant_dir / batch_id
    for path in (base, tenant_dir, directory):
        if path.is_symlink() or not path.is_dir():
            raise TypographyPilotError("batch_missing", "Existing private pilot batch was not found.")
    return directory


def _read_regular(path: Path, limit: int) -> bytes:
    """Refuse symlinks, devices, oversized blobs and missing protected files."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, ValueError) as exc:
        raise TypographyPilotError("slot_unavailable", "Protected pilot evidence is unavailable.") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > limit:
            raise TypographyPilotError("slot_unavailable", "Protected pilot evidence has invalid size or type.")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read(limit + 1)
    finally:
        os.close(fd)


def _inspect_locked(
    directory: Path, *, slot: int, plan_sha: str,
) -> dict[str, Any]:
    image_path = directory / f"design_{slot:02}.png"
    manifest_path = directory / f"slot_{slot:02}.json"
    review_path = directory / f"review_{slot:02}.json"
    if review_path.is_symlink() or review_path.exists():
        raise TypographyPilotError("already_reviewed", "This slot already has an immutable decision.")
    try:
        record = json.loads(_read_regular(manifest_path, _MAX_MANIFEST))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise TypographyPilotError("manifest_invalid", "The pilot manifest is malformed.") from exc
    if not isinstance(record, dict) or any((
        record.get("schema_version") != PILOT_SCHEMA,
        record.get("slot") != slot,
        record.get("plan_sha256") != plan_sha,
        record.get("technical_pass") is not True,
        record.get("native_imagegen_verified") is not True,
        record.get("review") != "pending",
        record.get("image_spelling_verified") is not False,
        record.get("eligible_for_publication") is not False,
        record.get("stage1_output_saved") is not False,
    )):
        raise TypographyPilotError("manifest_stale", "The slot manifest no longer matches its approved source.")
    data = _read_regular(image_path, _MAX_PNG)
    digest = sha256(data).hexdigest()
    if not _SHA256.fullmatch(str(record.get("sha256", ""))) or record["sha256"] != digest:
        raise TypographyPilotError("image_tampered", "Image bytes differ from the immutable pilot manifest.")
    qa = inspect_typography_png(data)
    if qa.get("technical_pass") is not True:
        raise TypographyPilotError("g5_png_rejected", "The staged image no longer passes technical QA.")
    return {
        "slot": slot, "sha256": digest,
        "image_path": str(image_path),
        "review_path": str(review_path),
        "technical_pass": True, "review": "pending",
        "eligible_for_publication": False, "stage1_output_saved": False,
    }


def _locked_slot(
    *, settings: Any, tenant_id: str, batch_id: str, slot: int,
    plan: Mapping[str, Any], callback: Callable[[Path, str], dict],
) -> dict:
    if not enabled(settings, tenant_id):
        raise TypographyPilotError("pilot_disabled", "Review is disabled for this canary.")
    if type(slot) is not int or not 1 <= slot <= 6:
        raise TypographyPilotError("slot_invalid", "Exactly one slot 1-6 is required.")
    plan_sha = fingerprint(plan)
    directory = _existing_batch_dir(settings, tenant_id, batch_id)
    lock_path = directory.parent.parent / ".imagegen-single-flight.lock"
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    except OSError as exc:
        raise TypographyPilotError("staging_unsafe", "The native pilot lock was not found.") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TypographyPilotError("imagegen_busy", "An ImageGen pilot or review is already running.") from exc
        return callback(directory, plan_sha)
    finally:
        os.close(fd)


def inspect_one_slot(
    *, settings: Any, tenant_id: str, batch_id: str,
    slot: int, plan: Mapping[str, Any],
) -> dict:
    """Read-only inspection metadata; an operator must separately inspect PNG."""
    return _locked_slot(
        settings=settings, tenant_id=tenant_id, batch_id=batch_id,
        slot=slot, plan=plan,
        callback=lambda directory, plan_sha: _inspect_locked(directory, slot=slot, plan_sha=plan_sha),
    )


def review_one_slot(
    *, settings: Any, tenant_id: str, batch_id: str,
    slot: int, plan: Mapping[str, Any], reviewer_id: str,
    decision: str, expected_sha256: str, image_spelling_verified: bool = False,
    originality_verified: bool = False, embroidery_verified: bool = False,
    reason_code: str | None = None,
    revalidate: Callable[[], Mapping[str, Any]],
) -> dict:
    """Write one append-only decision. 'accepted' is NOT publish authorization.

    Callers must authenticate/authorize the human reviewer outside this module;
    this local pilot is not exposed as an HTTP endpoint or scheduled worker.
    """
    if not isinstance(reviewer_id, str) or not 1 <= len(reviewer_id.strip()) <= 128:
        raise TypographyPilotError("reviewer_invalid", "An operator identity is required.")
    if decision not in ("accepted", "rejected"):
        raise TypographyPilotError("decision_invalid", "Choose accepted or rejected.")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise TypographyPilotError("sha256_invalid", "An exact image SHA-256 must be provided.")
    if decision == "accepted":
        if any(flag is not True for flag in (
            image_spelling_verified, originality_verified, embroidery_verified,
        )) or reason_code is not None:
            raise TypographyPilotError("human_qa_missing", "All three explicit human checks are required.")
    elif reason_code not in REJECT_REASONS or any((
        image_spelling_verified, originality_verified, embroidery_verified,
    )):
        raise TypographyPilotError("rejection_reason_invalid", "Reject with a fixed reason, without approval flags.")

    def commit_review(directory: Path, plan_sha: str) -> dict:
        inspected = _inspect_locked(directory, slot=slot, plan_sha=plan_sha)
        if inspected["sha256"] != expected_sha256:
            raise TypographyPilotError("image_changed", "The inspected image SHA-256 has changed.")
        if not enabled(settings, tenant_id) or fingerprint(revalidate()) != plan_sha:
            raise TypographyPilotError("source_changed", "The canary, keyword, reference rights or DNA has changed.")
        record = {
            "schema_version": REVIEW_SCHEMA, "slot": slot,
            "plan_sha256": plan_sha, "sha256": inspected["sha256"],
            "decision": decision, "reviewer_id": reviewer_id.strip(),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "image_spelling_verified": decision == "accepted",
            "originality_verified": decision == "accepted",
            "embroidery_verified": decision == "accepted",
            "reason_code": reason_code if decision == "rejected" else "approved",
            "eligible_for_publication": False, "stage1_output_saved": False,
        }
        try:
            _store_new(Path(inspected["review_path"]), (
                json.dumps(record, sort_keys=True) + "\n"
            ).encode("utf-8"))
        except FileExistsError as exc:
            raise TypographyPilotError("already_reviewed", "This slot already has a decision.") from exc
        return {
            "status": "review_recorded", "slot": slot, "decision": decision,
            "sha256": inspected["sha256"], "published": False,
            "eligible_for_publication": False,
        }

    return _locked_slot(
        settings=settings, tenant_id=tenant_id, batch_id=batch_id,
        slot=slot, plan=plan, callback=commit_review,
    )
