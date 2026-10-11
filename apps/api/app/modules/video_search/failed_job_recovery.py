"""Conservative recovery of failed Video Analyze jobs.

Never retries invalid inputs or known Gemini 4xx; each failed job can receive
one automatic recovery unless its source was subsequently refreshed. Provider
quota, source health and proxy disk capacity remain authoritative gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.processing.model import ProcessingJobModel

# Only transient failures that can recover without changing source content.
RECOVERABLE_VIDEO_CODES = frozenset({
    "gemini_video_transport_error",
    "video_proxy_timeout",
    "video_proxy_storage_exhausted",
})
_MARKER = "_video_recovery_requested_at"


def recover_video_failures(
    session: Session, tenant_id: str, *, limit: int = 20,
    apply: bool = False, storage_ready: bool = False,
) -> dict[str, int]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    candidates = list(session.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == "video_analyze",
        ProcessingJobModel.status == "failed",
        ProcessingJobModel.last_error_code.in_(tuple(RECOVERABLE_VIDEO_CODES)),
    ).order_by(ProcessingJobModel.updated_at, ProcessingJobModel.id).limit(limit * 4)))
    results = dict(eligible=0, recovered=0, needs_disk=0, inaccessible=0,
                   already_retried=0)
    moment = datetime.now(timezone.utc)
    for job in candidates:
        if results["eligible"] >= limit:
            break
        if job.last_error_code == "video_proxy_storage_exhausted" and not storage_ready:
            results["needs_disk"] += 1
            continue
        if job.payload_json and job.payload_json.get(_MARKER):
            results["already_retried"] += 1
            continue
        payload = dict(job.payload_json or {})
        source_id = payload.get("source_asset_id")
        if not isinstance(source_id, str) or not source_id:
            results["inaccessible"] += 1
            continue
        row = session.execute(select(SourceAssetModel, ExternalSourceModel).join(
            ExternalSourceModel,
            (SourceAssetModel.tenant_id == ExternalSourceModel.tenant_id)
            & (SourceAssetModel.external_source_id == ExternalSourceModel.id),
        ).where(
            SourceAssetModel.tenant_id == tenant_id,
            SourceAssetModel.id == source_id,
            SourceAssetModel.deleted_at.is_(None),
            ExternalSourceModel.status == "active",
            ExternalSourceModel.oauth_connection_id.is_not(None),
        )).first()
        if row is None:
            results["inaccessible"] += 1
            continue
        results["eligible"] += 1
        if not apply:
            continue
        payload[_MARKER] = moment.isoformat()
        job.payload_json = payload
        job.status = "retry"
        job.next_attempt_at = moment
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.completed_at = None
        job.cancellation_requested = False
        job.cancel_requested_at = None
        job.cancel_requested_by = None
        job.cancellation_reason = None
        job.max_attempts = max(job.max_attempts, job.attempt_count + 1)
        job.last_error_code = "video_recovery_requested"
        job.last_error_message = "Safe retry after a transient provider/proxy failure."
        job.updated_at = moment
        results["recovered"] += 1
    if apply and results["recovered"]:
        session.flush()
    return results
