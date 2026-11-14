"""Phase 4C.3E source identity is server-attested only, never agent self-approval."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.modules.agent_harness.fenced_source_provenance import (
    _EVIDENCE_KEY, _PROTOCOL, _signature, verify_server_source_capture,
)
from app.modules.processing.model import ProcessingJobModel


def _case(tmp_path):
    now = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)
    claim = now - timedelta(seconds=40)
    agent = "08e344a6-24b1-40d0-88c3-900e1fc28a1a"
    source_url = "https://i.etsystatic.com/17890876/r/il/sample.jpg"
    source_bytes = b"source snapshot from a server-only test fixture"
    digest = hashlib.sha256(source_bytes).hexdigest()
    url_digest = hashlib.sha256(source_url.encode()).hexdigest()
    job = ProcessingJobModel(
        tenant_id="tenant-source-qa", id="7d71689b-3a0f-47c3-9c83-00eb45fb19c6",
        job_type="etsy_design_extract", status="processing",
        attempt_count=2, max_attempts=4, cancellation_requested=False,
        claimed_by="etsy-image:" + agent, claimed_at=claim,
        lease_expires_at=now + timedelta(minutes=1),
        payload_json={
            "schema": "cam-etsy-extraction-v1",
            "execution_mode": "orchestrator_v1",
            "source": {"type": "etsy_cdn", "url": source_url},
            "source_url_sha256": url_digest,
        },
    )
    unsigned = {
        "protocol": _PROTOCOL, "tenant_id": job.tenant_id,
        "job_id": job.id, "agent_id": agent,
        "attempt_count": job.attempt_count,
        "claim_started_at": claim.isoformat(timespec="microseconds"),
        "source_url_sha256": url_digest, "source_sha256": digest,
        "source_bytes": len(source_bytes),
        "captured_epoch": int((now - timedelta(seconds=10)).timestamp()),
    }
    signing_key = bytes(range(32))
    proof = {**unsigned, "signature": _signature(signing_key, unsigned)}
    job.payload_json = {**job.payload_json, _EVIDENCE_KEY: proof}
    folder = tmp_path / "fenced-source-v1" / digest[:2]
    folder.mkdir(parents=True)
    (folder / f"{digest}.bin").write_bytes(source_bytes)
    params = dict(
        agent_id=agent, reported_source_sha256=digest,
        signing_key=signing_key, storage_root=tmp_path,
        enabled=True, emergency_stop=False, now=now,
    )
    return job, params, proof, source_bytes


def test_missing_independent_source_evidence_blocks_every_output(tmp_path):
    job, params, proof, raw = _case(tmp_path)
    assert verify_server_source_capture(job, **{**params, "enabled": False}).state == "feature_disabled"
    assert verify_server_source_capture(job, **{**params, "emergency_stop": True}).state == "emergency_stop"
    assert verify_server_source_capture(job, **{**params, "signing_key": None}).state == "signing_key_unavailable"
    assert verify_server_source_capture(job, **{
        **params, "reported_source_sha256": "not-a-sha",
    }).state == "source_hash_invalid"
    job.payload_json = {k: v for k, v in job.payload_json.items() if k != _EVIDENCE_KEY}
    assert verify_server_source_capture(job, **params).state == "server_capture_missing"


def test_matching_server_snapshot_only_verifies_source_not_browser_or_provider(tmp_path):
    job, params, proof, raw = _case(tmp_path)
    result = verify_server_source_capture(job, **params)
    assert result.state == "source_capture_verified"
    assert result.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.source_snapshot_verified
    assert not result.browser_provenance_verified
    assert not result.provider_provenance_verified
    assert not result.may_accept_output


def test_signature_bindings_reject_forged_client_hash_and_epoch(tmp_path):
    job, params, proof, raw = _case(tmp_path)
    proof["source_sha256"] = "a" * 64
    assert verify_server_source_capture(job, **params).state == "server_capture_signature_invalid"
    proof["source_sha256"] = params["reported_source_sha256"]
    proof["attempt_count"] = 1
    proof["signature"] = _signature(
        params["signing_key"], {k: v for k, v in proof.items() if k != "signature"},
    )
    assert verify_server_source_capture(job, **params).state == "server_capture_binding_invalid"
    proof["attempt_count"] = 2
    proof["signature"] = _signature(
        params["signing_key"], {k: v for k, v in proof.items() if k != "signature"},
    )
    job.attempt_count = 3
    assert verify_server_source_capture(job, **params).state == "server_capture_binding_invalid"


def test_source_origin_and_snapshot_storage_fail_closed(tmp_path):
    job, params, proof, raw = _case(tmp_path)
    job.payload_json["source"]["url"] = "https://untrusted.example.com/file.png"
    assert verify_server_source_capture(job, **params).state == "source_job_not_verified"
    job.payload_json["source"]["url"] = "https://i.etsystatic.com/17890876/r/il/sample.jpg"
    blob = tmp_path / "fenced-source-v1" / params["reported_source_sha256"][:2] / (
        params["reported_source_sha256"] + ".bin"
    )
    blob.write_bytes(b"tampered")
    assert verify_server_source_capture(job, **params).state == "source_snapshot_missing_or_corrupt"
    blob.unlink()
    blob.symlink_to(tmp_path / "other-source")
    assert verify_server_source_capture(job, **params).state == "source_snapshot_missing_or_corrupt"
    blob.unlink()
    parent = tmp_path / "fenced-source-v1"
    parent.rename(tmp_path / "captured")
    parent.symlink_to(tmp_path / "captured", target_is_directory=True)
    assert verify_server_source_capture(job, **params).state == "source_storage_unsafe"


def test_expired_source_proof_and_cancelled_job_fail_closed(tmp_path):
    job, params, proof, raw = _case(tmp_path)
    later = params["now"] + timedelta(hours=1)
    job.lease_expires_at = later + timedelta(seconds=30)
    assert verify_server_source_capture(job, **{**params, "now": later}).state == "server_capture_binding_invalid"
    job.cancellation_requested = True
    assert verify_server_source_capture(job, **params).state == "source_job_not_verified"
