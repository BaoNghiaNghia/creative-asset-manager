"""Phase 4C.3E inert source-capture evidence verifier.

The Image Agent's source hash is NOT independent verification of source bytes.
Only a server-signed record bound to an active claim and an on-disk immutable
source snapshot can pass this narrow source check. Browser/provider provenance
is a SEPARATE gate; this result never authorizes receipt acceptance.

No issuer, route, downloader or worker calls this module. Default OFF.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.modules.etsy_spy.service import verified_image_url
from app.modules.processing.model import ProcessingJobModel
from .executor_slots import _utc
from .fenced_storage import read_fenced_blob_pinned


_EVIDENCE_KEY = "_cam_fenced_source_capture_v1"
_PROTOCOL = "cam-fenced-source-capture-v1"
_MAX_AGE_SECONDS = 1800
_FIELDS = frozenset({
    "protocol", "tenant_id", "agent_id", "job_id", "attempt_count",
    "claim_started_at", "source_url_sha256", "source_sha256",
    "source_bytes", "captured_epoch", "signature",
})
_SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class SourceCaptureVerdict:
    state: str
    source_sha256: str | None = None
    source_snapshot_verified: bool = False
    browser_provenance_verified: bool = False
    provider_provenance_verified: bool = False
    may_accept_output: bool = False


def _signature(key: bytes, unsigned: dict[str, Any]) -> str:
    message = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hmac.new(
        key, b"cam-fenced-source-capture-v1:" + message, hashlib.sha256,
    ).hexdigest()


def verify_server_source_capture(
    job: ProcessingJobModel, *, agent_id: str,
    reported_source_sha256: str, signing_key: bytes | None,
    storage_root: Path | None, enabled: bool = False,
    emergency_stop: bool = True, now: datetime | None = None,
) -> SourceCaptureVerdict:
    if enabled is not True:
        return SourceCaptureVerdict("feature_disabled")
    if emergency_stop is not False:
        return SourceCaptureVerdict("emergency_stop")
    if not isinstance(signing_key, bytes) or len(signing_key) != 32:
        return SourceCaptureVerdict("signing_key_unavailable")
    if not isinstance(reported_source_sha256, str) or not _SHA.fullmatch(reported_source_sha256):
        return SourceCaptureVerdict("source_hash_invalid")
    clock = _utc(now) or datetime.now(timezone.utc)
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    source = payload.get("source")
    source_url = (
        verified_image_url(source.get("url"))
        if isinstance(source, dict) and source.get("type") == "etsy_cdn"
        else None
    )
    source_url_sha = hashlib.sha256(source_url.encode()).hexdigest() if source_url else None
    claimed = _utc(job.claimed_at)
    expiry = _utc(job.lease_expires_at)
    if (
        job.job_type != "etsy_design_extract"
        or job.status != "processing"
        or job.cancellation_requested
        or job.claimed_by != f"etsy-image:{agent_id}"
        or type(job.attempt_count) is not int or job.attempt_count < 1
        or type(job.max_attempts) is not int
        or job.attempt_count > job.max_attempts
        or claimed is None or claimed > clock + timedelta(seconds=5)
        or expiry is None or expiry <= clock or expiry <= claimed
        or payload.get("execution_mode") != "orchestrator_v1"
        or source_url is None
        or payload.get("source_url_sha256") != source_url_sha
    ):
        return SourceCaptureVerdict("source_job_not_verified")

    proof = payload.get(_EVIDENCE_KEY)
    if not isinstance(proof, dict) or set(proof) != _FIELDS:
        return SourceCaptureVerdict("server_capture_missing")
    signed = proof.get("signature")
    unsigned = {name: value for name, value in proof.items() if name != "signature"}
    if (
        not isinstance(signed, str) or not _SHA.fullmatch(signed)
        or not hmac.compare_digest(signed, _signature(signing_key, unsigned))
    ):
        return SourceCaptureVerdict("server_capture_signature_invalid")

    if (
        proof.get("protocol") != _PROTOCOL
        or proof.get("tenant_id") != job.tenant_id
        or proof.get("job_id") != job.id
        or proof.get("agent_id") != agent_id
        or type(proof.get("attempt_count")) is not int
        or proof["attempt_count"] != job.attempt_count
        or proof.get("claim_started_at") != claimed.isoformat(timespec="microseconds")
        or proof.get("source_url_sha256") != source_url_sha
        or proof.get("source_sha256") != reported_source_sha256
        or type(proof.get("source_bytes")) is not int
        or not 0 < proof["source_bytes"] <= 22_000_000
        or type(proof.get("captured_epoch")) is not int
        or not int(claimed.timestamp()) <= proof["captured_epoch"]
        or proof["captured_epoch"] > int(clock.timestamp()) + 2
        or int(clock.timestamp()) - proof["captured_epoch"] > _MAX_AGE_SECONDS
    ):
        return SourceCaptureVerdict("server_capture_binding_invalid")
    if (
        not isinstance(storage_root, Path)
        or not storage_root.is_absolute()
        or not storage_root.is_dir()
        or storage_root.is_symlink()
    ):
        return SourceCaptureVerdict("source_storage_unavailable")
    namespace = storage_root / "fenced-source-v1"
    shard = namespace / reported_source_sha256[:2]
    if namespace.is_symlink() or shard.is_symlink():
        return SourceCaptureVerdict("source_storage_unsafe")
    target = shard / f"{reported_source_sha256}.bin"
    if target.is_symlink() or read_fenced_blob_pinned(
        storage_root, namespace="fenced-source-v1",
        key=reported_source_sha256,
        expected_sha256=reported_source_sha256,
        expected_bytes=proof["source_bytes"],
    ) is None:
        return SourceCaptureVerdict("source_snapshot_missing_or_corrupt")

    return SourceCaptureVerdict(
        "source_capture_verified",
        source_sha256=reported_source_sha256,
        source_snapshot_verified=True,
        browser_provenance_verified=False,
        provider_provenance_verified=False,
        may_accept_output=False,
    )
