from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.visual_search.coverage_repository import visual_source_streamable
from app.modules.visual_search.lifecycle import VISUAL_EMBEDDING_SCHEMA_VERSION


RECOVERABLE_VISUAL_ERROR_CODES = frozenset({
    "visual_index_source_unavailable",
    "visual_index_failed",
    "visual_index_elasticsearch_unavailable",
    "visual_index_unconfigured",
    "visual_encoder_queue_full",
    "visual_encoder_queue_timeout",
})
_RECOVERY_MARKER = "_visual_recovery_requested_at"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _marker(payload: dict) -> datetime | None:
    value = payload.get(_RECOVERY_MARKER)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _aware(parsed)


def recover_failed_visual_jobs(
    session,
    *,
    tenant_id: str,
    limit: int,
    now: datetime | None = None,
) -> int:
    """Revive bounded failed Visual Search jobs when their source is usable again.

    A job is automatically recovered once after this fix. If it fails again,
    it is only revived after the external source row changes (for example after
    a reconnect), preventing a permanently unavailable item from looping.
    """
    if limit <= 0:
        return 0
    moment = now or datetime.now(timezone.utc)
    candidates = list(session.scalars(
        select(ProcessingJobModel)
        .where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == "visual_index_sync",
            ProcessingJobModel.status == "failed",
            ProcessingJobModel.last_error_code.in_(tuple(RECOVERABLE_VISUAL_ERROR_CODES)),
        )
        .order_by(ProcessingJobModel.updated_at, ProcessingJobModel.id)
        .limit(min(5000, max(limit * 4, limit)))
    ))
    if not candidates:
        return 0

    # The original source link may have been deleted or deauthorized while
    # another live source still contains the same content-addressed asset.
    # Recovery must select a streamable link, never retry the dead link forever.
    asset_ids = {str(job.entity_id) for job in candidates}
    if not asset_ids:
        return 0

    rows = session.execute(
        select(
            SourceAssetModel,
            AssetSourceLinkModel.asset_id,
            AssetModel.content_hash,
            ExternalSourceModel,
        )
        .join(
            AssetSourceLinkModel,
            (AssetSourceLinkModel.source_asset_id == SourceAssetModel.id)
            & (AssetSourceLinkModel.tenant_id == SourceAssetModel.tenant_id),
        )
        .join(
            AssetModel,
            (AssetModel.id == AssetSourceLinkModel.asset_id)
            & (AssetModel.tenant_id == AssetSourceLinkModel.tenant_id),
        )
        .join(
            ExternalSourceModel,
            (ExternalSourceModel.id == SourceAssetModel.external_source_id)
            & (ExternalSourceModel.tenant_id == SourceAssetModel.tenant_id),
        )
        .where(
            SourceAssetModel.tenant_id == tenant_id,
            AssetSourceLinkModel.asset_id.in_(asset_ids),
            SourceAssetModel.deleted_at.is_(None),
        )
    ).all()
    current: dict[tuple[str, str], list[tuple[str, ExternalSourceModel]]] = {}
    for source, asset_id, content_hash, external in rows:
        if visual_source_streamable(external):
            current.setdefault((str(asset_id), str(content_hash or "")), []).append(
                (source.id, external)
            )

    recovered = 0
    for job in candidates:
        if recovered >= limit:
            break
        payload = dict(job.payload_json or {})
        source_asset_id = str(payload.get("source_asset_id") or "")
        content_hash = str(payload.get("content_sha256") or "")
        choices = current.get((str(job.entity_id), content_hash), [])
        if not choices:
            continue
        # Preserve the previous link when healthy; switch only if it is no
        # longer usable. Stable ordering avoids oscillating retry identities.
        choices.sort(key=lambda choice: (choice[0] != source_asset_id, choice[0]))
        selected_source_id, external = choices[0]
        if payload.get("embedding_schema_version") != VISUAL_EMBEDDING_SCHEMA_VERSION:
            continue

        previous_recovery = _marker(payload)
        source_changed_at = _aware(external.updated_at)
        same_source_retried = (
            payload.get("_visual_recovery_source_asset_id") == selected_source_id
        )
        if previous_recovery is not None and same_source_retried and (
            source_changed_at is None or source_changed_at <= previous_recovery
        ):
            continue

        payload["source_asset_id"] = selected_source_id
        payload["_visual_recovery_source_asset_id"] = selected_source_id
        payload[_RECOVERY_MARKER] = moment.isoformat()
        job.payload_json = payload
        job.status = "retry"
        job.next_attempt_at = moment
        job.completed_at = None
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.cancellation_requested = False
        job.cancel_requested_at = None
        job.cancel_requested_by = None
        job.cancellation_reason = None
        if job.attempt_count >= job.max_attempts:
            job.max_attempts = job.attempt_count + 1
        job.last_error_code = "visual_index_recovery_requested"
        job.last_error_message = "Visual Search recovery requested after source availability check."
        job.updated_at = moment
        recovered += 1

    if recovered:
        session.flush()
    return recovered
