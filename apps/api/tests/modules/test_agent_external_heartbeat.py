"""Phase 4B.2A: authenticated external heartbeat and bounded signed evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.agent_harness.external_heartbeat import (
    EVIDENCE_FIELD, external_heartbeat_signing_key,
    issue_external_heartbeat, observe_external_heartbeat,
)
from app.modules.agent_harness.router import router as harness_router
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.etsy_spy.image_agent import _lease_token, image_router
from app.modules.etsy_spy.model import EtsySpyAgent, EtsySpyPicked
from app.modules.etsy_spy.service import IMAGE_AGENT_NAME_PREFIX, create_agent
from app.modules.processing.model import ProcessingJobModel


_SECRET = "a" * 64


@pytest.fixture
def setup(monkeypatch):
    from app.modules.agent_harness import router as harness_module
    from app.modules.etsy_spy import image_agent as image_module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        EtsySpyAgent.__table__, EtsySpyPicked.__table__,
        ProcessingJobModel.__table__,
    ])
    db = Session(engine, expire_on_commit=False)
    agent, secret = create_agent(db, "boostgame", IMAGE_AGENT_NAME_PREFIX + "A")
    spy, spy_token = create_agent(db, "boostgame", "Scout")
    db.add(EtsySpyPicked(tenant_id="boostgame", listing_key="1234567890"))
    now = datetime.now(timezone.utc)
    job = ProcessingJobModel(
        id=str(uuid4()), tenant_id="boostgame", job_type="etsy_design_extract",
        entity_type="etsy_listing", entity_id="1234567890",
        idempotency_key=str(uuid4()), status="processing", payload_json={},
        attempt_count=1, max_attempts=5,
        claimed_by="etsy-image:" + agent.id,
        claimed_at=now - timedelta(seconds=10),
        lease_expires_at=now + timedelta(seconds=100),
        updated_at=now - timedelta(seconds=10),
    )
    db.add(job)
    db.commit()
    principal = SimpleNamespace(
        active_tenant_id="boostgame", user_id="operator",
        effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}),
        platform_admin=False,
    )
    flags = SimpleNamespace(
        AGENT_HARNESS_READ_API_ENABLED=True,
        AI_EMERGENCY_STOP_ENABLED=False,
    )
    app = FastAPI()
    app.include_router(image_router, prefix="/api/v1/etsy-spy")
    app.include_router(harness_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    monkeypatch.setattr(image_module, "get_settings", lambda: flags)
    monkeypatch.setattr(harness_module, "get_settings", lambda: flags)
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED", "1")
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_SIGNING_KEY", _SECRET)
    device = f"/api/v1/etsy-spy/image-agents/{agent.id}/harness-heartbeat"
    admin = f"/api/v1/admin/agent-harness/boostgame/jobs/{job.id}/external-heartbeat"
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client, db=db, job=job, agent=agent,
            spy=spy, spy_token=spy_token, secret=secret,
            device=device, admin=admin, principal=principal, flags=flags,
        )
    db.close()
    engine.dispose()


def payload(s, **updates):
    body = {
        "protocol": "cam-agent-external-hb-v1",
        "job_id": s.job.id,
        "lease_token": _lease_token(s.agent, s.job),
        "attempt_count": s.job.attempt_count,
    }
    body.update(updates)
    return body


def send(s, body=None, *, token=None, route=None):
    return s.client.post(
        route or s.device, json=payload(s) if body is None else body,
        headers={"Authorization": "Bearer " + (token if token is not None else s.secret)},
    )


def test_heartbeat_ack_is_signed_and_does_not_renew_or_dispatch(setup):
    s = setup
    before_lease = s.job.lease_expires_at
    before_attempts = s.job.attempt_count
    response = send(s)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "authenticated_heartbeat_observed"
    assert body["heartbeat_id"] and len(body["heartbeat_id"]) == 32
    assert body["attempt_count"] == 1
    assert body["lease_renewed"] is False
    assert body["dispatch_allowed"] is False
    assert body["executor_slot_reserved"] is False
    s.db.refresh(s.job)
    assert s.job.lease_expires_at == before_lease
    assert s.job.attempt_count == before_attempts
    assert s.job.claimed_by == "etsy-image:" + s.agent.id
    assert s.db.query(ProcessingJobModel).count() == 1
    assert set(s.job.payload_json) == {EVIDENCE_FIELD}
    assert "nonce" not in response.text
    assert "signature" not in response.text
    assert s.secret not in response.text
    assert s.agent.id not in response.text
    viewed = s.client.get(s.admin)
    assert viewed.status_code == 200, viewed.text
    obs = viewed.json()
    assert obs["worker_heartbeat_authenticated"] is True
    assert obs["state"] == "authenticated_heartbeat_observed"
    assert 0 <= obs["heartbeat_age_seconds"] <= 5
    assert 1 <= obs["heartbeat_expires_in_seconds"] <= 45
    assert obs["dispatch_allowed"] is False
    assert "nonce" not in viewed.text
    assert "signature" not in viewed.text
    assert s.agent.id not in viewed.text


def _complete_after_signed_heartbeat(s):
    assert send(s).status_code == 200
    payload_with_proof = dict(s.job.payload_json)
    payload_with_proof["result"] = {
        "status": "needs_ai",
        "receipt_id": "f" * 40,
    }
    s.job.payload_json = payload_with_proof
    s.job.status = "completed"
    s.job.completed_at = datetime.now(timezone.utc)
    s.job.lease_expires_at = None
    s.db.commit()


def test_terminal_result_preserves_signed_historical_evidence(setup):
    s = setup
    _complete_after_signed_heartbeat(s)
    response = s.client.get(s.admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["worker_heartbeat_authenticated"] is False
    assert body["state"] == "job_not_processing"
    audit = body["historical_attestation"]
    assert audit["historical_heartbeat_verified"] is True
    assert audit["state"] == "signed_historical_heartbeat_verified"
    assert audit["worker_heartbeat_authenticated"] is False
    assert audit["dispatch_allowed"] is False
    assert audit["executor_slot_reserved"] is False
    assert audit["historical_receipt_age_seconds"] >= 0
    assert s.agent.id not in response.text
    assert "signature" not in response.text
    assert "nonce" not in response.text
    assert "lease_token" not in response.text


def test_historical_signature_fails_after_attempt_change_or_tampering(setup):
    s = setup
    _complete_after_signed_heartbeat(s)
    payload = dict(s.job.payload_json)
    proof = dict(payload[EVIDENCE_FIELD])
    proof["issued_epoch"] -= 20
    s.job.payload_json = {**payload, EVIDENCE_FIELD: proof}
    s.db.commit()
    assert s.client.get(s.admin).json()["historical_attestation"]["state"] == "signature_invalid"
    s.job.payload_json = payload
    s.job.attempt_count += 1
    s.db.commit()
    altered = s.client.get(s.admin).json()
    assert altered["historical_attestation"]["state"] == "job_binding_changed"
    s.job.attempt_count -= 1
    s.db.commit()
    s.agent.active = False
    s.db.commit()
    # Revocation now must NOT turn historical signing proof into a live slot,
    # but cannot erase the cryptographic fact that this former device reported.
    revoked = s.client.get(s.admin).json()
    assert revoked["worker_heartbeat_authenticated"] is False
    assert revoked["historical_attestation"]["historical_heartbeat_verified"] is True


def test_capacity_preview_is_tenant_scoped_and_never_reserves_slots(setup):
    s = setup
    url = "/api/v1/admin/agent-harness/boostgame/external-executors/etsy-image/capacity-preview"
    before = s.client.get(url)
    assert before.status_code == 200, before.text
    original = before.json()
    assert original["open_job_counts"]["processing"] == 1
    assert original["device_count"] == 1
    assert original["active_claim_count"] == 1
    assert original["authenticated_live_claim_count"] == 0
    assert original["verified_free_slots"] == 0
    assert original["dispatch_allowed"] is False
    assert original["reservation_created"] is False
    assert original["device_details"][0]["capacity_state"] == "busy_unverified"
    assert s.agent.id not in before.text
    assert s.secret not in before.text

    assert send(s).status_code == 200
    with_live = s.client.get(url).json()
    assert with_live["capacity_state"] == "read_only_evidence_available"
    assert with_live["authenticated_live_claim_count"] == 1
    assert with_live["device_details"][0]["capacity_state"] == "verified_busy"
    assert with_live["device_details"][0]["verified_free_slots"] == 0
    assert with_live["dispatch_allowed"] is False
    assert s.db.query(ProcessingJobModel).count() == 1

    payload_with_proof = dict(s.job.payload_json)
    payload_with_proof["result"] = {"status": "needs_ai", "receipt_id": "f" * 40}
    s.job.payload_json = payload_with_proof
    s.job.status = "completed"
    s.job.lease_expires_at = None
    s.job.completed_at = datetime.now(timezone.utc)
    s.db.commit()
    after = s.client.get(url).json()
    assert after["open_job_counts"]["processing"] == 0
    assert after["device_details"][0]["capacity_state"] == "idle_job_bound_heartbeat_unavailable"
    assert after["authenticated_live_claim_count"] == 0
    assert after["historical_evidence"]["verified_signed_completed"] == 1
    assert after["historical_evidence"]["recent_verified_job_ids"] == [s.job.id]
    assert after["verified_free_slots"] == 0
    assert after["dispatch_allowed"] is False
    assert "signature" not in str(after)


def test_historical_proof_survives_expiry_but_not_bad_completion(setup):
    from app.modules.agent_harness.external_heartbeat import inspect_completed_heartbeat

    s = setup
    _complete_after_signed_heartbeat(s)
    three_days_later = datetime.now(timezone.utc) + timedelta(days=3)
    attested = inspect_completed_heartbeat(
        s.job, signing_key=bytes.fromhex(_SECRET),
        agent_registered=True, now=three_days_later,
    )
    assert attested["historical_heartbeat_verified"] is True
    assert attested["worker_heartbeat_authenticated"] is False
    assert attested["dispatch_allowed"] is False
    assert attested["historical_receipt_age_seconds"] >= 3 * 86400 - 5

    s.job.completed_at = s.job.claimed_at - timedelta(seconds=1)
    s.db.commit()
    assert s.client.get(s.admin).json()["historical_attestation"]["state"] == (
        "completion_boundary_invalid"
    )
    s.job.completed_at = datetime.now(timezone.utc)
    s.job.payload_json = {
        **s.job.payload_json,
        "result": {"status": "needs_ai", "receipt_id": "bad"},
    }
    s.db.commit()
    assert s.client.get(s.admin).json()["historical_attestation"]["state"] == (
        "completed_result_missing"
    )


def test_capacity_device_limit_fails_closed(setup):
    s = setup
    for i in range(20):
        create_agent(s.db, "boostgame", IMAGE_AGENT_NAME_PREFIX + f"extra-{i:02d}")
    s.db.commit()
    response = s.client.get(
        "/api/v1/admin/agent-harness/boostgame/external-executors/"
        "etsy-image/capacity-preview"
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["capacity_state"] == "device_limit_exceeded"
    assert result["device_count"] == 21
    assert result["device_details"] == []
    assert result["verified_free_slots"] == 0
    assert result["dispatch_allowed"] is False


def test_capacity_preview_limits_burst_claims_and_hides_other_tenants(setup):
    s = setup
    url = "/api/v1/admin/agent-harness/boostgame/external-executors/etsy-image/capacity-preview"
    another = ProcessingJobModel(
        id=str(uuid4()), tenant_id="boostgame", job_type="etsy_design_extract",
        entity_type="etsy_listing", entity_id="1234567890",
        idempotency_key=str(uuid4()), status="processing", payload_json={},
        attempt_count=1, max_attempts=5,
        claimed_by="etsy-image:" + s.agent.id,
        claimed_at=datetime.now(timezone.utc)-timedelta(seconds=7),
        lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=80),
    )
    foreign = ProcessingJobModel(
        id=str(uuid4()), tenant_id="other", job_type="etsy_design_extract",
        entity_type="etsy_listing", entity_id="999",
        idempotency_key=str(uuid4()), status="processing", payload_json={},
        attempt_count=1, max_attempts=5, claimed_by="etsy-image:unknown",
        claimed_at=datetime.now(timezone.utc)-timedelta(seconds=7),
        lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=80),
    )
    s.db.add_all([another, foreign])
    s.db.commit()
    result = s.client.get(url)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["open_job_counts"]["processing"] == 2
    assert body["device_details"][0]["capacity_state"] == "conflicting_processing_claims"
    assert body["capacity_state"] == "occupied_with_conflict"
    assert body["verified_free_slots"] == 0
    assert foreign.id not in result.text
    s.principal.effective_permissions = frozenset({"ai_operations.read"})
    assert s.client.get(url).status_code == 403
    s.principal.effective_permissions = frozenset({"ai_operations.read", "ai_provider.configure"})
    s.principal.active_tenant_id = "other"
    assert s.client.get(url).status_code == 403
    s.principal.active_tenant_id = "boostgame"
    s.flags.AGENT_HARNESS_READ_API_ENABLED = False
    assert s.client.get(url).status_code == 404
    s.flags.AGENT_HARNESS_READ_API_ENABLED = True
    s.flags.AI_EMERGENCY_STOP_ENABLED = True
    assert s.client.get(url).json()["capacity_state"] == "emergency_stop"


def test_feature_and_key_fail_closed_without_state_mutation(setup, monkeypatch):
    s = setup
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED", "0")
    assert send(s).status_code == 404
    assert s.client.get(s.admin).json()["worker_heartbeat_authenticated"] is False
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED", "1")
    monkeypatch.delenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_SIGNING_KEY")
    assert send(s).status_code == 503
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_SIGNING_KEY", "f" * 32)
    assert send(s).status_code == 503
    assert s.db.get(ProcessingJobModel, s.job.id).payload_json == {}
    assert external_heartbeat_signing_key() is None


@pytest.mark.parametrize("changes,code", [
    ({"lease_token": "0" * 64}, 409),
    ({"attempt_count": 2}, 409),
    ({"attempt_count": True}, 422),
    ({"attempt_count": "1"}, 422),
    ({"protocol": "v2"}, 422),
    ({"extra": True}, 422),
    ({"job_id": "missing"}, 422),
])
def test_reject_untrusted_payloads(setup, changes, code):
    s = setup
    response = send(s, payload(s, **changes))
    assert response.status_code == code, response.text
    assert s.job.payload_json == {}


@pytest.mark.parametrize("change,reason", [
    ({"status": "retry"}, "job_not_owned_or_cancelled"),
    ({"status": "completed"}, "job_not_owned_or_cancelled"),
    ({"cancellation_requested": True}, "job_not_owned_or_cancelled"),
    ({"claimed_by": "etsy-image:unknown"}, "job_not_owned_or_cancelled"),
    ({"attempt_count": 2}, "job_attempt_changed"),
    ({"attempt_count": 6}, "job_attempt_changed"),
    ({"lease_expires_at": None}, "job_lease_not_active"),
    ({"lease_expires_at": datetime.now(timezone.utc)-timedelta(seconds=1)},
     "job_lease_not_active"),
    ({"lease_expires_at": datetime.now(timezone.utc)+timedelta(seconds=1)},
     "job_lease_near_expiry"),
])
def test_job_state_is_fenced(setup, change, reason):
    s = setup
    original = payload(s)
    for k, v in change.items():
        setattr(s.job, k, v)
    s.db.commit()
    if "attempt_count" in change:
        # Prove that even a currently matching per-attempt token cannot
        # authenticate an old expected_attempt number.
        original["lease_token"] = _lease_token(s.agent, s.job)
    rejected = send(s, original)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == reason
    assert EVIDENCE_FIELD not in s.job.payload_json


def test_unpicked_job_cannot_heartbeat(setup):
    s = setup
    s.db.delete(s.db.get(EtsySpyPicked, ("boostgame", "1234567890")))
    s.db.commit()
    assert send(s).status_code == 409
    assert EVIDENCE_FIELD not in s.job.payload_json


def test_wrong_role_revoked_device_and_other_tenant_are_rejected(setup):
    s = setup
    assert send(s, token=s.spy_token).status_code == 401
    rogue = send(s, route=f"/api/v1/etsy-spy/image-agents/{s.spy.id}/harness-heartbeat",
                 token=s.spy_token)
    assert rogue.status_code == 403
    foreign = str(uuid4())
    s.db.add(ProcessingJobModel(
        id=foreign, tenant_id="other", job_type="etsy_design_extract",
        entity_type="etsy_listing", entity_id="1234567890",
        idempotency_key=str(uuid4()), payload_json={},
        status="processing", attempt_count=1, max_attempts=5,
        claimed_by="etsy-image:" + s.agent.id,
        claimed_at=datetime.now(timezone.utc)-timedelta(seconds=5),
        lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=30),
    ))
    s.db.commit()
    assert send(s, payload(s, job_id=foreign)).status_code == 404
    admin_other = s.admin.replace(s.job.id, foreign)
    assert s.client.get(admin_other).status_code == 404
    assert s.client.get(admin_other.replace("/boostgame/", "/other/")).status_code == 403
    s.agent.active = False
    s.db.commit()
    assert send(s).status_code == 401


def test_signed_heartbeat_is_fenced_after_reclaim_revocation_and_tampering(setup):
    s = setup
    assert send(s).status_code == 200
    assert s.client.get(s.admin).json()["worker_heartbeat_authenticated"] is True
    evidence = dict(s.job.payload_json[EVIDENCE_FIELD])
    original = dict(s.job.payload_json)
    # Tampering with the record cannot forge a valid server-only HMAC.
    evidence["expires_epoch"] += 3
    s.job.payload_json = {EVIDENCE_FIELD: evidence}
    s.db.commit()
    assert s.client.get(s.admin).json()["state"] == "signature_invalid"

    # Replacing it with the valid signed value cannot survive a new attempt.
    s.job.payload_json = original
    s.job.attempt_count += 1
    s.job.claimed_at = datetime.now(timezone.utc)
    s.db.commit()
    assert s.client.get(s.admin).json()["state"] == "job_binding_changed"
    s.job.attempt_count -= 1
    s.db.commit()
    s.agent.active = False
    s.db.commit()
    assert s.client.get(s.admin).json()["worker_heartbeat_authenticated"] is False


def test_signature_expires_and_cannot_be_replayed_for_dispatch(setup):
    s = setup
    assert send(s).status_code == 200
    future = datetime.now(timezone.utc) + timedelta(seconds=50)
    viewed = observe_external_heartbeat(
        s.job, signing_key=bytes.fromhex(_SECRET),
        agent_active=True, now=future,
    )
    assert viewed["worker_heartbeat_authenticated"] is False
    assert viewed["dispatch_allowed"] is False
    assert viewed["state"] == "heartbeat_expired"


def test_admin_rbac_tenant_flag_and_emergency_stop(setup):
    s = setup
    s.principal.effective_permissions = frozenset({"ai_operations.read"})
    assert s.client.get(s.admin).status_code == 403
    s.principal.effective_permissions = frozenset({"ai_operations.read", "ai_provider.configure"})
    s.principal.active_tenant_id = "other"
    assert s.client.get(s.admin).status_code == 403
    s.principal.active_tenant_id = "boostgame"
    s.flags.AGENT_HARNESS_READ_API_ENABLED = False
    assert s.client.get(s.admin).status_code == 404
    s.flags.AGENT_HARNESS_READ_API_ENABLED = True
    s.flags.AI_EMERGENCY_STOP_ENABLED = True
    assert send(s).status_code == 503
    assert s.client.get(s.admin).status_code == 200
    s.flags.AI_EMERGENCY_STOP_ENABLED = False
    assert s.client.get(s.admin.replace(s.job.id, "invalid")).status_code == 422


def test_paired_probe_exposes_readiness_boolean_without_signing_material(
    setup, monkeypatch,
):
    s = setup
    endpoint = s.device.replace("/harness-heartbeat", "/probe")
    auth = {"Authorization": "Bearer " + s.secret}
    ready = s.client.get(endpoint, headers=auth)
    assert ready.status_code == 200, ready.text
    payload = ready.json()
    assert payload["harness_heartbeat_supported"] is True
    assert payload["harness_heartbeat_ready"] is True
    assert "a" * 64 not in ready.text
    assert s.secret not in ready.text
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED", "0")
    assert s.client.get(endpoint, headers=auth).json()["harness_heartbeat_ready"] is False
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED", "1")
    monkeypatch.delenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_SIGNING_KEY")
    assert s.client.get(endpoint, headers=auth).json()["harness_heartbeat_ready"] is False
