"""Inert Phase 4C.3F: server-owned, public-IP-pinned Etsy source capture.

No API route, cron or agent worker calls this module. A job must already hold
an authorized 0209 slot and exact fencing token. Default OFF/emergency ON.
A server-resolved public IP is pinned to the TLS socket; SNI/hostname checking
uses the exact allowlisted Etsy CDN host. Never follow redirects, use
environment proxies, fetch metadata IPs or trust caller-provided source bytes.
The server HMAC record attests ONLY fetched bytes, NOT browser/provider output.

This is a bounded prototype: the HTTPS fetch occurs while the caller-owned
SQL row lock is held, so no live route until async two-stage admission is
reviewed. No external request is made unless explicitly invoked and enabled.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.modules.etsy_spy.service import verified_image_url
from app.modules.processing.model import ProcessingJobModel
from .executor_slots import _preflight, _utc, verify_image_output_fence
from .fenced_source_provenance import (
    _EVIDENCE_KEY, _PROTOCOL, _signature, verify_server_source_capture,
)
from .fenced_storage import (
    MAX_FENCED_BLOB_BYTES, read_fenced_blob_pinned, store_fenced_blob_once,
)

MAX_SOURCE_BYTES = MAX_FENCED_BLOB_BYTES
_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_PATH = re.compile(r"/[A-Za-z0-9/._~%+@-]{0,1300}\Z")


@dataclass(frozen=True)
class CaptureDecision:
    state: str
    source_sha256: str | None = None
    source_bytes: int | None = None
    browser_verified: bool = False
    provider_verified: bool = False
    may_accept_output: bool = False


def _resolve_public_address(host: str) -> str:
    """Reject any non-public DNS answer; connect only to the resolved public IP."""
    answers = socket.getaddrinfo(
        host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    if not answers:
        raise ValueError("dns_empty")
    addresses: list[str] = []
    for family, kind, protocol, canon, sockaddr in answers:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise ValueError("dns_not_public")
        addresses.append(str(address))
    return sorted(set(addresses))[0]


def _verify_source_image(raw: bytes, mime: str) -> bool:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        return False
    if mime not in _IMAGE_MIMES:
        return False
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                return False
            if image.width < 1 or image.height < 1 or image.width * image.height > 50_000_000:
                return False
            expected = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
            if expected[image.format] != mime:
                return False
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
        return True
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return False


def _fetch_etsy_source(source_url: str) -> bytes:
    """Bounded direct TLS GET, pinned to a public numeric IP address."""
    if not isinstance(source_url, str) or verified_image_url(source_url) != source_url:
        raise ValueError("source_url_not_allowlisted")
    parsed = urlsplit(source_url)
    path = parsed.path or "/"
    if not _PATH.fullmatch(path):
        raise ValueError("source_path_invalid")
    host = parsed.hostname
    if host not in {"i.etsystatic.com", "img.etsystatic.com"}:
        raise ValueError("source_host_invalid")
    address = _resolve_public_address(host)
    # Numeric connect address prevents DNS rebinding. TLS still validates the
    # server certificate for the ORIGINAL allowed hostname via SNI.
    with socket.create_connection((address, 443), timeout=5.0) as connection:
        connection.settimeout(7.0)
        with ssl.create_default_context().wrap_socket(
            connection, server_hostname=host,
        ) as tls:
            request = (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                "Accept: image/jpeg,image/png,image/webp\r\n"
                "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
            )
            tls.sendall(request.encode("ascii"))
            response = http.client.HTTPResponse(tls)
            response.begin()
            if response.status != 200:  # redirects fail: never follow 3xx
                raise ValueError("unexpected_http_status")
            mime = (response.getheader("content-type") or "").split(";", 1)[0].strip().lower()
            if mime not in _IMAGE_MIMES:
                raise ValueError("unexpected_content_type")
            if (response.getheader("content-encoding") or "identity").lower() != "identity":
                raise ValueError("compressed_http_body_forbidden")
            length = response.getheader("content-length")
            if length is not None:
                if not length.isascii() or not length.isdecimal() or int(length) > MAX_SOURCE_BYTES:
                    raise ValueError("source_content_length_invalid")
            raw = bytearray()
            deadline = time.monotonic() + 12.0
            while len(raw) <= MAX_SOURCE_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("source_read_deadline")
                tls.settimeout(min(3.0, remaining))
                # read1 returns currently available bytes instead of trying to
                # fill 64 KiB in a potentially unbounded slow-drip response.
                chunk = response.read1(min(65536, MAX_SOURCE_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > MAX_SOURCE_BYTES:
                raise ValueError("source_too_large")
            if length is not None and len(raw) != int(length):
                raise ValueError("source_truncated")
            result = bytes(raw)
            if not _verify_source_image(result, mime):
                raise ValueError("source_image_invalid")
            return result


def capture_server_source_for_existing_slot(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    fencing_token: int, signing_key: bytes | None,
    storage_root: Path | None = None,
    enabled: bool = False, emergency_stop: bool = True,
    now: datetime | None = None,
) -> CaptureDecision:
    """Capture an already-owned exact job; caller commits after file fsync.

    SQL rollback may leave an immutable orphan. Never sign agent-provided
    bytes, and never infer browser/provider readiness or print rights.
    """
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return CaptureDecision(blocked)
    if emergency_stop is not False:
        return CaptureDecision("emergency_stop")
    if not isinstance(signing_key, bytes) or len(signing_key) != 32:
        return CaptureDecision("signing_key_unavailable")
    if not isinstance(storage_root, Path) or not storage_root.is_absolute():
        return CaptureDecision("source_storage_unavailable")

    clock = _utc(now) or datetime.now(timezone.utc)
    proof = verify_image_output_fence(
        session, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
        fencing_token=fencing_token, enabled=True, emergency_stop=False,
        now=clock,
    )
    if proof.state != "fence_verified":
        return CaptureDecision(proof.state)
    job = session.get(ProcessingJobModel, job_id)
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    source = payload.get("source")
    url = (
        verified_image_url(source.get("url"))
        if isinstance(source, dict) and source.get("type") == "etsy_cdn"
        else None
    )
    if (
        payload.get("execution_mode") != "orchestrator_v1"
        or url is None
        or payload.get("source_url_sha256") != hashlib.sha256(url.encode()).hexdigest()
    ):
        return CaptureDecision("source_job_not_verified")

    existing = payload.get(_EVIDENCE_KEY)
    if existing is not None:
        if not isinstance(existing, dict):
            return CaptureDecision("source_capture_requires_reconcile")
        digest = existing.get("source_sha256")
        verdict = verify_server_source_capture(
            job, agent_id=agent_id, reported_source_sha256=digest,
            signing_key=signing_key, storage_root=storage_root,
            enabled=True, emergency_stop=False, now=clock,
        )
        if verdict.state == "source_capture_verified":
            return CaptureDecision(
                "already_captured", source_sha256=digest,
                source_bytes=existing["source_bytes"],
            )
        return CaptureDecision("source_capture_requires_reconcile")

    try:
        raw = _fetch_etsy_source(url)
    except (ValueError, OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
        return CaptureDecision("source_fetch_not_verified")
    digest = hashlib.sha256(raw).hexdigest()
    # Recheck at end of bounded network IO; job and slot locks remain held.
    completed_at = _utc(now) if now is not None else datetime.now(timezone.utc)
    after = verify_image_output_fence(
        session, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
        fencing_token=fencing_token, enabled=True, emergency_stop=False,
        now=completed_at,
    )
    if after.state != "fence_verified":
        return CaptureDecision(after.state)
    if not store_fenced_blob_once(
        storage_root, namespace="fenced-source-v1",
        key=digest, expected_sha256=digest, raw=raw,
    ):
        return CaptureDecision("source_storage_unavailable")
    if read_fenced_blob_pinned(
        storage_root, namespace="fenced-source-v1",
        key=digest, expected_sha256=digest, expected_bytes=len(raw),
    ) is None:
        return CaptureDecision("source_storage_not_verified")

    unsigned = {
        "protocol": _PROTOCOL, "tenant_id": tenant_id, "agent_id": agent_id,
        "job_id": job_id, "attempt_count": job.attempt_count,
        "claim_started_at": _utc(job.claimed_at).isoformat(timespec="microseconds"),
        "source_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "source_sha256": digest, "source_bytes": len(raw),
        "captured_epoch": int(completed_at.timestamp()),
    }
    signed = {**unsigned, "signature": _signature(signing_key, unsigned)}
    job.payload_json = {**payload, _EVIDENCE_KEY: signed}
    session.flush()
    return CaptureDecision("captured", source_sha256=digest, source_bytes=len(raw))
