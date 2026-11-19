"""Phase 4C.3G: default-OFF, staged source capture with NO SQL network lock.

Prepare: short job+device+slot locked transaction, then ROLLBACK AND CLOSE.
Fetch: one bounded public-IP-pinned HTTPS request and immutable storage while
NO SQL Session is in scope.
Finalize: a fresh job+device+slot lock, exact source/epoch revalidation, pinned
blob check, signed manifest and caller-owned transaction commit.

The in-process semaphore prevents bursts per API process, NOT fleet-wide.
There is NO live caller, HTTP route, worker or Windows agent integration.
This source-only proof never attests browser/provider execution or print rights.
"""
from __future__ import annotations

import hashlib
import http.client
import threading
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.modules.etsy_spy.service import verified_image_url
from app.modules.processing.model import ProcessingJobModel

from .executor_slots import _preflight, _utc, verify_image_output_fence
from .fenced_source_capture import (
    CaptureDecision, _fetch_etsy_source, _verify_source_image, MAX_SOURCE_BYTES,
)
from .fenced_source_provenance import (
    _EVIDENCE_KEY, _PROTOCOL, _signature, verify_server_source_capture,
)
from .fenced_storage import read_fenced_blob_pinned, store_fenced_blob_once

# Only an advisory in-process limit. A live scheduler needs a durable, global
# capacity gate before it can safely invoke this prototype on multiple hosts.
_CAPTURE_SLOTS = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class SourceCapturePlan:
    tenant_id: str
    agent_id: str
    job_id: str
    attempt_count: int
    claim_started_at: datetime
    fencing_token: int
    source_url: str
    source_url_sha256: str


def _current_source(job: ProcessingJobModel) -> tuple[str, str] | None:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    item = payload.get("source")
    url = (
        verified_image_url(item.get("url"))
        if isinstance(item, dict) and item.get("type") == "etsy_cdn"
        else None
    )
    digest = hashlib.sha256(url.encode()).hexdigest() if url else None
    if (
        not url or not digest or payload.get("execution_mode") != "orchestrator_v1"
        or payload.get("source_url_sha256") != digest
    ):
        return None
    return url, digest


def _existing_capture(
    job: ProcessingJobModel, *, agent_id: str,
    signing_key: bytes, storage_root: Path, now: datetime,
) -> CaptureDecision | None:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    existing = payload.get(_EVIDENCE_KEY)
    if existing is None:
        return None
    if not isinstance(existing, dict):
        return CaptureDecision("source_capture_requires_reconcile")
    digest = existing.get("source_sha256")
    verdict = verify_server_source_capture(
        job, agent_id=agent_id, reported_source_sha256=digest,
        signing_key=signing_key, storage_root=storage_root,
        enabled=True, emergency_stop=False, now=now,
    )
    if verdict.state == "source_capture_verified":
        return CaptureDecision(
            "already_captured", source_sha256=digest,
            source_bytes=existing["source_bytes"],
        )
    # In particular, a signed record for an earlier epoch cannot be
    # silently overwritten, even when source bytes happen to match.
    return CaptureDecision("source_capture_requires_reconcile")


def _plan_under_lock(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    fencing_token: int, signing_key: bytes, storage_root: Path,
    now: datetime,
) -> tuple[SourceCapturePlan | None, CaptureDecision | None]:
    fence = verify_image_output_fence(
        session, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
        fencing_token=fencing_token, enabled=True, emergency_stop=False,
        now=now,
    )
    if fence.state != "fence_verified":
        return None, CaptureDecision(fence.state)

    job = session.get(ProcessingJobModel, job_id)
    if job is None or job.tenant_id != tenant_id:
        return None, CaptureDecision("source_job_not_verified")
    source = _current_source(job)
    if source is None:
        return None, CaptureDecision("source_job_not_verified")
    existing = _existing_capture(
        job, agent_id=agent_id, signing_key=signing_key,
        storage_root=storage_root, now=now,
    )
    if existing is not None:
        return None, existing
    url, url_sha = source
    return SourceCapturePlan(
        tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
        attempt_count=job.attempt_count,
        claim_started_at=_utc(job.claimed_at),
        fencing_token=fencing_token,
        source_url=url, source_url_sha256=url_sha,
    ), None


def _valid_fetched_image(raw: bytes) -> bool:
    # Recheck even when the private source downloader is replaced by a test
    # stub. No raw bytes supplied by an Image Agent are accepted here.
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        return False
    import io
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            media = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(picture.format)
        return media is not None and _verify_source_image(raw, media)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return False


def _finalize_under_lock(
    session: Session, *, plan: SourceCapturePlan,
    digest: str, size: int,
    signing_key: bytes, storage_root: Path, now: datetime,
) -> CaptureDecision:
    if not isinstance(plan, SourceCapturePlan):
        return CaptureDecision("capture_plan_invalid")
    fence = verify_image_output_fence(
        session, tenant_id=plan.tenant_id, agent_id=plan.agent_id,
        job_id=plan.job_id, fencing_token=plan.fencing_token,
        enabled=True, emergency_stop=False, now=now,
    )
    if fence.state != "fence_verified":
        return CaptureDecision(fence.state)

    job = session.get(ProcessingJobModel, plan.job_id)
    current = _current_source(job) if job is not None else None
    if (
        job is None or job.tenant_id != plan.tenant_id
        or job.attempt_count != plan.attempt_count
        or _utc(job.claimed_at) != plan.claim_started_at
        or current != (plan.source_url, plan.source_url_sha256)
    ):
        return CaptureDecision("capture_plan_stale")
    previous = _existing_capture(
        job, agent_id=plan.agent_id, signing_key=signing_key,
        storage_root=storage_root, now=now,
    )
    if previous is not None:
        if previous.state == "already_captured" and previous.source_sha256 != digest:
            return CaptureDecision("source_capture_requires_reconcile")
        return previous

    # Re-check file bytes on a pinned FD. A crash between fetching and signing
    # is a safe orphan, not a server-signed result.
    data = read_fenced_blob_pinned(
        storage_root, namespace="fenced-source-v1",
        key=digest, expected_sha256=digest, expected_bytes=size,
    )
    if data is None:
        return CaptureDecision("source_storage_not_verified")

    unsigned = {
        "protocol": _PROTOCOL, "tenant_id": plan.tenant_id,
        "agent_id": plan.agent_id, "job_id": plan.job_id,
        "attempt_count": plan.attempt_count,
        "claim_started_at": plan.claim_started_at.isoformat(timespec="microseconds"),
        "source_url_sha256": plan.source_url_sha256,
        "source_sha256": digest, "source_bytes": size,
        "captured_epoch": int(now.timestamp()),
    }
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    job.payload_json = {
        **payload, _EVIDENCE_KEY: {
            **unsigned, "signature": _signature(signing_key, unsigned),
        },
    }
    session.flush()
    return CaptureDecision(
        "captured", source_sha256=digest, source_bytes=size,
    )


def capture_server_source_in_stages(
    session_factory: Callable[[], Session], *,
    tenant_id: str, agent_id: str, job_id: str,
    fencing_token: int, signing_key: bytes | None,
    storage_root: Path | None = None,
    enabled: bool = False, emergency_stop: bool = True,
    now: datetime | None = None,
) -> CaptureDecision:
    """Orchestrate the only safe local capture path, with isolated SQL phases.

    Source HTTP and fsync run AFTER the first Session has closed and BEFORE
    creating the final Session. The semaphore is nonblocking. The final
    transaction revalidates all authoritative claim and source fields.
    """
    if enabled is not True:
        return CaptureDecision("feature_disabled")
    if emergency_stop is not False:
        return CaptureDecision("emergency_stop")
    if not isinstance(signing_key, bytes) or len(signing_key) != 32:
        return CaptureDecision("signing_key_unavailable")
    if (
        not isinstance(storage_root, Path) or not storage_root.is_absolute()
        or storage_root.is_symlink() or not storage_root.is_dir()
    ):
        return CaptureDecision("source_storage_unavailable")
    if not callable(session_factory):
        return CaptureDecision("session_factory_required")

    start = _utc(now) or datetime.now(timezone.utc)
    with session_factory() as session:
        blocked = _preflight(session, enabled=True)
        if blocked:
            return CaptureDecision(blocked)
        plan, early = _plan_under_lock(
            session, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
            fencing_token=fencing_token, signing_key=signing_key,
            storage_root=storage_root, now=start,
        )
        # No network or disk write can occur before the context closes and
        # the read transaction is explicitly rolled back.
        session.rollback()
    if early is not None:
        return early
    if plan is None:
        return CaptureDecision("source_job_not_verified")

    if not _CAPTURE_SLOTS.acquire(blocking=False):
        return CaptureDecision("source_capture_capacity_busy")
    try:
        try:
            raw = _fetch_etsy_source(plan.source_url)
        except (ValueError, OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
            return CaptureDecision("source_fetch_not_verified")
        if not _valid_fetched_image(raw):
            return CaptureDecision("source_image_invalid")
        digest = hashlib.sha256(raw).hexdigest()
        if not store_fenced_blob_once(
            storage_root, namespace="fenced-source-v1",
            key=digest, expected_sha256=digest, raw=raw,
        ):
            return CaptureDecision("source_storage_unavailable")
    finally:
        _CAPTURE_SLOTS.release()

    finish = _utc(now) or datetime.now(timezone.utc)
    with session_factory() as session:
        blocked = _preflight(session, enabled=True)
        if blocked:
            return CaptureDecision(blocked)
        outcome = _finalize_under_lock(
            session, plan=plan, digest=digest, size=len(raw),
            signing_key=signing_key, storage_root=storage_root,
            now=finish,
        )
        if outcome.state in {"captured", "already_captured"}:
            session.commit()
        else:
            session.rollback()
        return outcome
