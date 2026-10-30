"""G6.3 opt-in durable, private Stage 1 V2 output-history writer.

This uses a separate history stage and the picked Stage 0 keyword as the
logical owner. V1 image job rows, their output versions and Stage 2 source
plans remain unchanged. No ImageGen calls or automatic publication occur.

A single reviewed slot is committed at a time: managed storage first, then a
tenant-locked DB checkpoint. A crash between them is safe to retry because the
managed-storage asset ID is stable. Uncommitted storage bytes are never
advertised as outputs, and no cleanup/delete is attempted by this writer.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.providers.contracts import StoreAssetInput, StorageProviderError
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordVolumeModel,
)
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, enabled, fingerprint, resolve_plan,
)
from app.modules.realistic_review_ugc.typography_imagegen_review import (
    _existing_batch_dir, _read_regular,
)
from app.modules.realistic_review_ugc.typography_quality_gate import (
    inspect_typography_png,
)
from app.modules.realistic_review_ugc.typography_reviewed_batch import (
    MAX_PNG_BYTES, inspect_reviewed_batch,
)

HISTORY_STAGE = "typography_v2"
HISTORY_SCHEMA = "typography-v2/output-history-v1"
PNG_MIME = "image/png"
CANVAS_WIDTH = 2048
CANVAS_HEIGHT = 1024


def history_enabled(settings: Any, tenant_id: str) -> bool:
    """Distinct default-OFF publish gate, in addition to all ImageGen gates."""
    return bool(
        enabled(settings, tenant_id)
        and getattr(settings, "TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED", False) is True
        and getattr(settings, "MANAGED_ASSET_STORAGE_ENABLED", False) is True
    )


def _assert_history_row(
    row: RrugcImageOutputVersionModel, *,
    slot: int, image_sha256: str, size_bytes: int, plan_sha256: str,
) -> None:
    info = row.font_info_json
    if (not isinstance(info, dict)
            or info.get("schema_version") != HISTORY_SCHEMA
            or info.get("slot") != slot
            or info.get("png_sha256") != image_sha256
            or info.get("plan_sha256") != plan_sha256
            or row.content_type != PNG_MIME
            or row.size_bytes != size_bytes
            or row.width != CANVAS_WIDTH or row.height != CANVAS_HEIGHT
            or not isinstance(row.remote_file_id, str)
            or not 1 <= len(row.remote_file_id) <= 255):
        raise TypographyPilotError(
            "history_conflict",
            "Previously saved Typography V2 output does not match the reviewed image.",
        )


def _existing(
    session: Session, *, tenant_id: str, keyword_id: str,
    batch_id: str, slot: int,
) -> RrugcImageOutputVersionModel | None:
    rows = list(session.scalars(select(RrugcImageOutputVersionModel).where(
        RrugcImageOutputVersionModel.tenant_id == tenant_id,
        RrugcImageOutputVersionModel.stage == HISTORY_STAGE,
        RrugcImageOutputVersionModel.job_id == keyword_id,
        RrugcImageOutputVersionModel.processing_job_id == batch_id,
        RrugcImageOutputVersionModel.output_name == f"design_{slot:02}.png",
    ).limit(2)))
    if len(rows) > 1:
        raise TypographyPilotError("history_conflict", "Duplicate saved slot identity.")
    return rows[0] if rows else None


def _verify_current(
    session: Session, *, tenant_id: str, user_id: str,
    keyword_id: str, reference_id: str,
    source_keyword_exact: str, plan_sha256: str,
) -> None:
    # The keyword row serializes all V2 history writes for this Stage 0 term.
    # The original V1 job table is intentionally not locked or updated.
    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
    ).with_for_update())
    if keyword is None or keyword.picked is not True or keyword.keyword != source_keyword_exact:
        raise TypographyPilotError("source_changed", "The selected Stage 0 keyword changed.")
    current = resolve_plan(
        session, tenant_id=tenant_id, user_id=user_id,
        keyword_id=keyword_id, reference_id=reference_id,
    )
    if fingerprint(current) != plan_sha256:
        raise TypographyPilotError("source_changed", "Reference rights or accepted Style DNA changed.")


async def _stream_bytes(data: bytes):
    yield data


def _asset_id(tenant_id: str, keyword_id: str, batch_id: str, slot: int) -> str:
    # Bounded, deterministic, tenant-qualified ID for provider idempotency.
    tenant_hash = sha256(tenant_id.encode("utf-8")).hexdigest()[:20]
    return f"rrugc-typography-v2:{tenant_hash}:{keyword_id}:{batch_id}:{slot:02}"


async def save_reviewed_v2_outputs(
    *, settings: Any, tenant_id: str, user_id: str,
    keyword_id: str, reference_id: str, batch_id: str,
    selected_slots: Sequence[int],
    session_factory: Callable[[], Session],
    storage: Any,
) -> dict[str, Any]:
    """Durably save only explicitly reviewed slots into PRIVATE V2 history.

    Caller must authenticate the human operator and enforce publish RBAC.
    This service does not expose an HTTP/worker route or elevate permissions.
    Each iteration revalidates rights and PNG bytes; partial checkpoints are
    intentionally preserved if a later upload or database commit fails.
    """
    if not history_enabled(settings, tenant_id):
        raise TypographyPilotError("history_disabled", "Private V2 output history writer is disabled.")
    if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 255:
        raise TypographyPilotError("operator_invalid", "An authenticated operator is required.")
    if storage is None or not callable(getattr(storage, "store_asset", None)):
        raise TypographyPilotError("storage_unavailable", "Managed asset storage is not available.")

    def current_plan() -> Mapping[str, Any]:
        with session_factory() as session:
            return resolve_plan(
                session, tenant_id=tenant_id, user_id=user_id,
                keyword_id=keyword_id, reference_id=reference_id,
            )

    plan = current_plan()
    batch = inspect_reviewed_batch(
        settings=settings, tenant_id=tenant_id, batch_id=batch_id,
        plan=plan, revalidate=current_plan,
        keyword_id=keyword_id, selected_slots=selected_slots,
    )
    selected = batch["selection_preview"]["selected_images"]
    plan_sha = batch["plan_sha256"]
    private_dir = _existing_batch_dir(settings, tenant_id, batch_id)
    results: list[dict[str, Any]] = []

    for item in selected:
        if not history_enabled(settings, tenant_id):
            raise TypographyPilotError("history_disabled", "V2 history was disabled during upload.")
        slot = item["slot"]
        digest = item["sha256"]
        size = item["size_bytes"]
        # A previous read-only batch pass does not authorize unchecked later
        # writes. Read the original immutable PNG again without following links.
        data = _read_regular(private_dir / f"design_{slot:02}.png", MAX_PNG_BYTES)
        if len(data) != size or sha256(data).hexdigest() != digest:
            raise TypographyPilotError("image_tampered", "Reviewed PNG changed before persistence.")
        quality = inspect_typography_png(data)
        if quality.get("technical_pass") is not True or quality.get("sha256") != digest:
            raise TypographyPilotError("g5_png_rejected", "Reviewed PNG no longer passes G5 QA.")

        # Fast idempotent check before incurring a managed-storage upload.
        with session_factory() as session:
            with session.begin():
                _verify_current(
                    session, tenant_id=tenant_id, user_id=user_id,
                    keyword_id=keyword_id, reference_id=reference_id,
                    source_keyword_exact=batch["source_keyword_exact"], plan_sha256=plan_sha,
                )
                found = _existing(
                    session, tenant_id=tenant_id, keyword_id=keyword_id,
                    batch_id=batch_id, slot=slot,
                )
                if found is not None:
                    _assert_history_row(
                        found, slot=slot, image_sha256=digest,
                        size_bytes=size, plan_sha256=plan_sha,
                    )
                    results.append({"slot": slot, "version": found.version, "saved_new": False})
                    continue

        # Never hold a database row lock during network operations.
        # Provider-managed asset IDs are stable across retries/crashes.
        stored = await storage.store_asset(StoreAssetInput(
            tenant_id=tenant_id,
            asset_id=_asset_id(tenant_id, keyword_id, batch_id, slot),
            content_hash=digest, body=_stream_bytes(data), content_type=PNG_MIME,
            size_bytes=size, filename=f"typography_v2_{keyword_id}_{batch_id}_{slot:02}.png",
        ))
        if (not isinstance(stored.remote_file_id, str)
                or not 1 <= len(stored.remote_file_id) <= 255
                or stored.content_hash != digest
                or (stored.size_bytes is not None and stored.size_bytes != size)):
            raise StorageProviderError(
                "Managed storage did not return the verified PNG identity.",
                retryable=True, code="typography_v2_storage_identity_invalid",
            )

        if not history_enabled(settings, tenant_id):
            raise TypographyPilotError("history_disabled", "V2 history was disabled before commit.")
        # Lock and validate the authoritative source AFTER the network upload.
        # If rights were revoked mid-flight, the private uploaded object is
        # unreferenced and must not become a Stage 1 output.
        with session_factory() as session:
            with session.begin():
                _verify_current(
                    session, tenant_id=tenant_id, user_id=user_id,
                    keyword_id=keyword_id, reference_id=reference_id,
                    source_keyword_exact=batch["source_keyword_exact"], plan_sha256=plan_sha,
                )
                found = _existing(
                    session, tenant_id=tenant_id, keyword_id=keyword_id,
                    batch_id=batch_id, slot=slot,
                )
                if found is not None:
                    _assert_history_row(
                        found, slot=slot, image_sha256=digest,
                        size_bytes=size, plan_sha256=plan_sha,
                    )
                    results.append({"slot": slot, "version": found.version, "saved_new": False})
                    continue
                latest = session.scalar(select(RrugcImageOutputVersionModel.version).where(
                    RrugcImageOutputVersionModel.tenant_id == tenant_id,
                    RrugcImageOutputVersionModel.stage == HISTORY_STAGE,
                    RrugcImageOutputVersionModel.job_id == keyword_id,
                ).order_by(RrugcImageOutputVersionModel.version.desc()).limit(1))
                version = int(latest or 0) + 1
                session.add(RrugcImageOutputVersionModel(
                    tenant_id=tenant_id, stage=HISTORY_STAGE,
                    job_id=keyword_id, version=version,
                    processing_job_id=batch_id,
                    output_name=f"design_{slot:02}.png",
                    font_info_json={
                        "schema_version": HISTORY_SCHEMA,
                        "slot": slot, "png_sha256": digest,
                        "plan_sha256": plan_sha,
                        "reference_id": reference_id,
                        "selection_sha256": batch["selection_preview"]["selection_sha256"],
                    },
                    remote_file_id=stored.remote_file_id,
                    content_type=PNG_MIME, size_bytes=size,
                    width=CANVAS_WIDTH, height=CANVAS_HEIGHT,
                ))
                session.add(AuthAuditEventModel(
                    tenant_id=tenant_id, actor_id=user_id,
                    action="typography_v2.output_saved",
                    detail_json={
                        "keyword_id": keyword_id,
                        "batch_id": batch_id, "slot": slot,
                        "version": version, "png_sha256": digest,
                    },
                ))
                session.flush()
                results.append({"slot": slot, "version": version, "saved_new": True})

    return {
        "schema_version": HISTORY_SCHEMA,
        "keyword_id": keyword_id, "batch_id": batch_id,
        "saved_count": len(results),
        "new_count": sum(int(r["saved_new"]) for r in results),
        "items": results,
        "stage1_v2_history_saved": bool(results),
        "private_only": True,
        "stage2_handoff_performed": False,
        "published": False,
        "stage2_jobs_created": 0,
    }


def list_v2_history(
    session: Session, *, tenant_id: str, keyword_id: str,
) -> list[dict[str, Any]]:
    """Private metadata-only projection; caller must enforce read RBAC."""
    rows = list(session.scalars(select(RrugcImageOutputVersionModel).where(
        RrugcImageOutputVersionModel.tenant_id == tenant_id,
        RrugcImageOutputVersionModel.stage == HISTORY_STAGE,
        RrugcImageOutputVersionModel.job_id == keyword_id,
    ).order_by(RrugcImageOutputVersionModel.version.desc())))
    return [{
        "version": row.version,
        "slot": (row.font_info_json or {}).get("slot"),
        "batch_id": row.processing_job_id,
        "size_bytes": row.size_bytes,
        "width": row.width,
        "height": row.height,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "stage2_handoff_performed": False,
        "private_only": True,
    } for row in rows]
