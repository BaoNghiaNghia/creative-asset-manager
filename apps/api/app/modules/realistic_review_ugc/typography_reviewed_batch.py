"""G6.3 private, read-only slot aggregation for a reviewed ImageGen batch.

Prepares a truthful 1..6 slot checkpoint without writing Stage 1 history,
creating a source plan, uploading to Drive or triggering Stage 2. The only
records considered accepted are matching immutable G6.1/G6.2 evidence.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    PILOT_SCHEMA, TypographyPilotError, enabled, fingerprint,
)
from app.modules.realistic_review_ugc.typography_imagegen_review import (
    REVIEW_SCHEMA, REJECT_REASONS, _locked_slot, _read_regular,
)
from app.modules.realistic_review_ugc.typography_quality_gate import (
    SHA256_PATTERN, V2_EXPECTED_SLOTS, inspect_typography_png,
    summarize_v2_slots,
)

BATCH_SCHEMA = "typography-v2/private-reviewed-batch-v1"
SELECTION_SCHEMA = "typography-v2/private-reviewed-selection-preview-v1"
MAX_CHECKPOINT_BYTES = 32 * 1024
MAX_PNG_BYTES = 25 * 1024 * 1024


def _present(path: Path) -> bool:
    # A symlink is never an absent checkpoint, even when its target is missing.
    return path.exists() or path.is_symlink()


def _json_checkpoint(path: Path, *, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(_read_regular(path, MAX_CHECKPOINT_BYTES))
    except (ValueError, UnicodeError) as exc:
        raise TypographyPilotError(code, "Private pilot checkpoint cannot be decoded.") from exc
    if not isinstance(value, dict):
        raise TypographyPilotError(code, "Private pilot checkpoint must be an object.")
    return value


def _review_status(
    directory: Path, *, slot: int, plan_sha256: str, image_sha256: str,
) -> tuple[str, bool]:
    path = directory / f"review_{slot:02}.json"
    if not _present(path):
        return "pending", False
    record = _json_checkpoint(path, code="review_invalid")
    if any((
        record.get("schema_version") != REVIEW_SCHEMA,
        record.get("slot") != slot,
        record.get("plan_sha256") != plan_sha256,
        record.get("sha256") != image_sha256,
        record.get("eligible_for_publication") is not False,
        record.get("stage1_output_saved") is not False,
        not isinstance(record.get("reviewer_id"), str),
        not 1 <= len(str(record.get("reviewer_id") or "").strip()) <= 128,
    )):
        raise TypographyPilotError("review_invalid", "Human review does not match the image checkpoint.")
    try:
        reviewed_at = datetime.fromisoformat(record["reviewed_at"])
        if reviewed_at.tzinfo is None:
            raise ValueError("timezone required")
    except (ValueError, TypeError, KeyError) as exc:
        raise TypographyPilotError("review_invalid", "Human review timestamp is invalid.") from exc
    decision = record.get("decision")
    spelling = record.get("image_spelling_verified")
    originality = record.get("originality_verified")
    embroidery = record.get("embroidery_verified")
    if decision == "accepted":
        if (record.get("reason_code") != "approved" or
                (spelling, originality, embroidery) != (True, True, True)):
            raise TypographyPilotError("review_invalid", "Acceptance is missing mandatory human checks.")
        return decision, True
    if decision == "rejected":
        if (record.get("reason_code") not in REJECT_REASONS or
                (spelling, originality, embroidery) != (False, False, False)):
            raise TypographyPilotError("review_invalid", "Rejection reason or flags are invalid.")
        return decision, False
    raise TypographyPilotError("review_invalid", "Unknown human review decision.")


def _inspect_batch(
    directory: Path, *, source_keyword_exact: str, batch_id: str, plan_sha256: str,
) -> dict[str, Any]:
    slots: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for slot in range(1, V2_EXPECTED_SLOTS + 1):
        image_path = directory / f"design_{slot:02}.png"
        manifest_path = directory / f"slot_{slot:02}.json"
        review_path = directory / f"review_{slot:02}.json"
        available = tuple(_present(p) for p in (image_path, manifest_path, review_path))
        if available == (False, False, False):
            slots.append({"slot": slot, "status": "pending"})
            continue
        if not available[0] or not available[1]:
            raise TypographyPilotError("checkpoint_incomplete", "Image and manifest must exist together.")

        manifest = _json_checkpoint(manifest_path, code="manifest_invalid")
        if any((
            manifest.get("schema_version") != PILOT_SCHEMA,
            manifest.get("slot") != slot,
            manifest.get("plan_sha256") != plan_sha256,
            manifest.get("native_imagegen_verified") is not True,
            manifest.get("technical_pass") is not True,
            manifest.get("review") != "pending",
            manifest.get("image_spelling_verified") is not False,
            manifest.get("eligible_for_publication") is not False,
            manifest.get("stage1_output_saved") is not False,
        )):
            raise TypographyPilotError("manifest_stale", "G6.1 slot evidence differs from approved source.")
        expected_digest = manifest.get("sha256")
        if not isinstance(expected_digest, str) or not SHA256_PATTERN.fullmatch(expected_digest):
            raise TypographyPilotError("manifest_invalid", "G6.1 slot image checksum is invalid.")
        data = _read_regular(image_path, MAX_PNG_BYTES)
        digest = sha256(data).hexdigest()
        if digest != expected_digest:
            raise TypographyPilotError("image_tampered", "Image differs from the immutable G6.1 checksum.")
        quality = inspect_typography_png(data)
        if quality.get("technical_pass") is not True or quality.get("sha256") != digest:
            raise TypographyPilotError("g5_png_rejected", "Image does not pass G5 technical QA.")
        review, verified = _review_status(
            directory, slot=slot, plan_sha256=plan_sha256, image_sha256=digest,
        )
        slots.append({
            "slot": slot, "status": "generated", "sha256": digest,
            "technical_pass": True, "review": review,
            "image_spelling_verified": verified,
        })
        if review == "accepted":
            accepted.append({
                "slot": slot, "sha256": digest, "size_bytes": len(data),
                "private_image_path": str(image_path),
            })

    contract = summarize_v2_slots(source_keyword_exact, slots)
    # A G5 partial/complete checkpoint is still NOT permission to publish.
    return {
        "schema_version": BATCH_SCHEMA, "batch_id": batch_id,
        "plan_sha256": plan_sha256, "source_keyword_exact": source_keyword_exact,
        "requested_slots": V2_EXPECTED_SLOTS,
        "reviewed_images": accepted,
        "accepted_count": len(accepted),
        "slot_contract": contract,
        "stage1_output_saved": False,
        "stage2_handoff_performed": False,
        "eligible_for_publication": False,
        "published": False,
        "private_only": True,
    }



def _prepare_selection_preview(
    batch: Mapping[str, Any], *, tenant_id: str, keyword_id: str,
    selected_slots: Sequence[int],
) -> dict[str, Any]:
    """Bind explicitly chosen reviewed slots to an immutable, private intent.

    Called only inside the pilot lock after the full ImageGen/G5/G6.2
    checkpoint has been verified. This is NOT a storage/publication token:
    every future writer must independently revalidate source rights, PNG bytes,
    managed storage receipts, ownership and authorization.
    """
    if not isinstance(keyword_id, str) or not 1 <= len(keyword_id.strip()) <= 255:
        raise TypographyPilotError("selection_invalid", "A selected Stage 0 keyword ID is required.")
    if (not isinstance(selected_slots, (tuple, list))
            or not 1 <= len(selected_slots) <= V2_EXPECTED_SLOTS
            or any(type(slot) is not int or not 1 <= slot <= V2_EXPECTED_SLOTS
                   for slot in selected_slots)
            or len(set(selected_slots)) != len(selected_slots)):
        raise TypographyPilotError("selection_invalid", "Choose 1-6 unique numbered slots.")

    approved = batch.get("reviewed_images")
    contract = batch.get("slot_contract")
    if (not isinstance(approved, list)
            or not isinstance(contract, dict)
            or batch.get("accepted_count") != len(approved)
            or batch.get("private_only") is not True
            or any(batch.get(flag) is not False for flag in (
                "published", "stage1_output_saved", "stage2_handoff_performed",
                "eligible_for_publication",
            ))):
        raise TypographyPilotError("selection_checkpoint_invalid", "Reviewed batch cannot be selected.")
    by_slot: dict[int, dict] = {}
    hashes: set[str] = set()
    for image in approved:
        if not isinstance(image, dict):
            raise TypographyPilotError("selection_checkpoint_invalid", "Reviewed image is invalid.")
        number, digest, size = image.get("slot"), image.get("sha256"), image.get("size_bytes")
        if (type(number) is not int or not 1 <= number <= V2_EXPECTED_SLOTS
                or number in by_slot or not isinstance(digest, str)
                or not SHA256_PATTERN.fullmatch(digest) or digest in hashes
                or type(size) is not int or not 0 < size <= MAX_PNG_BYTES):
            raise TypographyPilotError("selection_checkpoint_invalid", "Reviewed image identity is invalid.")
        by_slot[number] = {"slot": number, "sha256": digest, "size_bytes": size}
        hashes.add(digest)
    if contract.get("eligible_slots") != sorted(by_slot):
        raise TypographyPilotError("selection_checkpoint_invalid", "Reviewed slot count differs from G5.")
    chosen = sorted(selected_slots)
    if any(slot not in by_slot for slot in chosen):
        raise TypographyPilotError("selection_not_approved", "Only explicitly accepted slots can be selected.")

    # Bind to tenant + Stage0 keyword + batch + exact reviewed PNG bytes.
    # The fingerprint is an idempotency HINT, never a public access capability.
    proof = {
        "schema_version": SELECTION_SCHEMA,
        "tenant_id": tenant_id, "keyword_id": keyword_id,
        "batch_id": batch["batch_id"], "plan_sha256": batch["plan_sha256"],
        "selected_images": [by_slot[number] for number in chosen],
    }
    digest = sha256(json.dumps(
        proof, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "schema_version": SELECTION_SCHEMA,
        "selection_sha256": digest,
        "selected_slots": chosen,
        "selected_count": len(chosen),
        "selected_images": proof["selected_images"],
        "requires_managed_storage_commit": True,
        "private_only": True,
        "published": False,
        "stage1_output_saved": False,
        "stage2_handoff_performed": False,
        "can_queue_stage2": False,
    }


def inspect_reviewed_batch(
    *, settings: Any, tenant_id: str, batch_id: str,
    plan: Mapping[str, Any], revalidate: Callable[[], Mapping[str, Any]],
    keyword_id: str | None = None,
    selected_slots: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Read-only, tenant-gated aggregate with source/rights revalidation.

    A caller must separately authenticate the operator before invoking this.
    There is intentionally no HTTP route, upload, archive or public URL.
    """
    if not callable(revalidate):
        raise TypographyPilotError("source_invalid", "Current source revalidation is required.")
    if not isinstance(tenant_id, str) or not enabled(settings, tenant_id):
        raise TypographyPilotError("pilot_disabled", "Batch inspection is disabled for this tenant.")
    keyword = plan.get("source_keyword_exact")
    if not isinstance(keyword, str) or not keyword.strip():
        raise TypographyPilotError("source_invalid", "The original keyword is unavailable.")

    def under_lock(directory: Path, plan_sha: str) -> dict:
        # Compare again after locking and after reading every checkpoint.
        if not enabled(settings, tenant_id) or fingerprint(revalidate()) != plan_sha:
            raise TypographyPilotError("source_changed", "Reference rights, keyword or Style DNA changed.")
        result = _inspect_batch(
            directory, source_keyword_exact=keyword,
            batch_id=batch_id, plan_sha256=plan_sha,
        )
        if not enabled(settings, tenant_id) or fingerprint(revalidate()) != plan_sha:
            raise TypographyPilotError("source_changed", "Reference rights, keyword or Style DNA changed.")
        if selected_slots is not None:
            result["selection_preview"] = _prepare_selection_preview(
                result, tenant_id=tenant_id, keyword_id=keyword_id,
                selected_slots=selected_slots,
            )
        return result

    return _locked_slot(
        settings=settings, tenant_id=tenant_id, batch_id=batch_id,
        slot=1, plan=plan, callback=under_lock,
    )
