"""Phase 4C.3E deterministic read-only receipt snapshots (no database needed)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.modules.agent_harness.fenced_output import (
    _receipt_identity, _stage_content_addressed, _location,
)
from app.modules.agent_harness.fenced_reconciliation import (
    classify_fenced_receipt_snapshot, _stored_blob_state,
)
from app.modules.agent_harness.model import (
    AgentImageOutputReceiptModel, AgentExecutorSlotModel,
)
from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.processing.model import ProcessingJobModel


def _snapshot(tmp_path):
    tenant = "audit-tenant"
    job_id = "d8b0efa7-b54f-463b-870d-cc137fe2b22b"
    agent_id = "3290c402-111f-4ef9-9840-a731c0c8ceff"
    at = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
    raw = b"PNG-sample-diagnostic-only"
    digest = hashlib.sha256(raw).hexdigest()
    uid = _receipt_identity(
        tenant_id=tenant, agent_id=agent_id, job_id=job_id,
        claim_attempt_count=1, claim_started_at=at, fencing_token=1,
        output_sha256=digest,
    )
    job = ProcessingJobModel(
        id=job_id, tenant_id=tenant, job_type="etsy_design_extract",
        status="processing", payload_json={},
        claimed_by="etsy-image:" + agent_id,
        claimed_at=at, lease_expires_at=at + timedelta(minutes=2),
        attempt_count=1, max_attempts=3,
        cancellation_requested=False,
    )
    row = AgentImageOutputReceiptModel(
        tenant_id=tenant, job_id=job_id, agent_id=agent_id,
        claim_attempt_count=1, claim_started_at=at, fencing_token=1,
        receipt_id=uid, output_sha256=digest,
        output_bytes=len(raw), source_sha256="b" * 64,
        engine="chatgpt_assisted", status="prepared",
        prepared_at=at, accepted_at=None,
    )
    slot = AgentExecutorSlotModel(
        tenant_id=tenant, agent_id=agent_id, job_id=job_id,
        claim_attempt_count=1, claim_started_at=at,
        fencing_token=1, lease_expires_at=at + timedelta(seconds=90),
    )
    agent = EtsySpyAgent(
        tenant_id=tenant, id=agent_id, name="CAM Image Agent / QA",
        token_hash="a" * 64, active=True,
    )
    return dict(
        tenant_id=tenant, job=job, row=row, slot=slot, agent=agent,
        storage_root=tmp_path, now=at + timedelta(seconds=15),
    ), raw


def _inspect(**params):
    return classify_fenced_receipt_snapshot(**params)


def test_prepared_transitions_only_to_observation_and_never_autoaccept(tmp_path):
    args, data = _snapshot(tmp_path)
    waiting = _inspect(**args)
    assert waiting.state == "prepared_awaiting_blob"
    assert waiting.manual_reconcile_required is False
    assert waiting.storage_ref == _location(args["row"].receipt_id)
    assert _stage_content_addressed(
        tmp_path, args["row"].receipt_id, data, args["row"].output_sha256,
    )
    stored = _inspect(**args)
    assert stored.state == "prepared_blob_recoverable"
    assert stored.manual_reconcile_required is False
    assert args["row"].status == "prepared"  # read-only


def test_stale_attempt_revoked_device_and_source_lease_never_autoaccept(tmp_path):
    args, data = _snapshot(tmp_path)
    assert _stage_content_addressed(
        tmp_path, args["row"].receipt_id, data, args["row"].output_sha256,
    )
    job = args["job"]
    job.attempt_count = 2
    assert _inspect(**args).state == "prepared_stale_epoch"
    job.attempt_count = 1
    args["agent"].active = False
    assert _inspect(**args).manual_reconcile_required
    args["agent"].active = True
    args["slot"].lease_expires_at = args["now"] - timedelta(seconds=1)
    assert _inspect(**args).state == "prepared_stale_epoch"
    args["slot"].lease_expires_at = args["now"] + timedelta(seconds=1)
    job.lease_expires_at = args["now"] - timedelta(seconds=1)
    assert _inspect(**args).state == "prepared_stale_epoch"
    job.lease_expires_at = args["now"] + timedelta(seconds=30)
    job.cancellation_requested = True
    assert _inspect(**args).state == "prepared_stale_epoch"


def test_accepted_requires_committed_metadata_and_present_unchanged_file(tmp_path):
    args, data = _snapshot(tmp_path)
    row, job = args["row"], args["job"]
    assert _stage_content_addressed(
        tmp_path, row.receipt_id, data, row.output_sha256,
    )
    row.status = "accepted"
    row.accepted_at = args["now"]
    job.status = "completed"
    job.lease_expires_at = None
    job.payload_json = {"result": {
        "status": "needs_review",
        "receipt_id": row.receipt_id,
        "fenced_storage_ref": _location(row.receipt_id),
        "output_sha256": row.output_sha256,
        "source_sha256": row.source_sha256,
        "engine": row.engine,
        "print_ready": False,
        "manual_qa_required": True,
        "rights_status": "unverified",
    }}
    assert _inspect(**args).state == "accepted_verified"
    # The original slot can be released without making accepted history invalid.
    args["slot"] = None
    args["agent"] = None
    assert _inspect(**args).state == "accepted_verified"
    result = job.payload_json["result"]
    result["rights_status"] = "verified"
    assert _inspect(**args).state == "accepted_metadata_mismatch"
    result["rights_status"] = "unverified"
    target = tmp_path / _location(row.receipt_id)
    target.write_bytes(b"corrupt")
    assert _inspect(**args).state == "accepted_blob_corrupt"
    target.unlink()
    assert _inspect(**args).state == "accepted_blob_missing"
    assert _inspect(**args).manual_reconcile_required


def test_conflicts_and_tenant_isolation_fail_closed(tmp_path):
    args, data = _snapshot(tmp_path)
    assert _inspect(**{**args, "tenant_id": "another"}).state == "job_not_found"
    assert _inspect(**{**args, "job": None}).state == "job_not_found"
    assert _inspect(**{**args, "row": None}).state == "receipt_not_found"
    row = args["row"]
    row.receipt_id = "0" * 64
    assert _inspect(**args).state == "receipt_integrity_mismatch"
    row.receipt_id = _receipt_identity(
        tenant_id=args["tenant_id"], agent_id=row.agent_id, job_id=row.job_id,
        claim_attempt_count=1, claim_started_at=row.claim_started_at,
        fencing_token=1, output_sha256=row.output_sha256,
    )
    row.status = "completed"
    assert _inspect(**args).state == "invalid_receipt_state"


def test_storage_symlinks_are_rejected(tmp_path):
    args, data = _snapshot(tmp_path)
    row = args["row"]
    location = tmp_path / _location(row.receipt_id)
    location.parent.mkdir(parents=True)
    location.symlink_to(tmp_path / "elsewhere")
    assert _stored_blob_state(tmp_path, row) == "blob_corrupt"
    location.unlink()
    location.parent.rmdir()
    (tmp_path / "fenced-v1").rename(tmp_path / "old-dir")
    (tmp_path / "fenced-v1").symlink_to(tmp_path / "old-dir", target_is_directory=True)
    assert _stored_blob_state(tmp_path, row) == "storage_path_unsafe"
