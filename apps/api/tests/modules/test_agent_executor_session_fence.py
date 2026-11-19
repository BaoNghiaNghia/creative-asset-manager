"""Phase 4C.2B: session fencing is fail-closed, not capacity attestation."""
from uuid import uuid4

import pytest

from app.modules.agent_harness.executor_session_fence import (
    SESSION_FENCE_FIELD, bind_session_fence, executor_session_fence_enabled,
    issue_session_fence, normalized_executor_session, session_fence_matches,
    session_fence_shape,
)


def _match(payload, session, *, tenant="boostgame", agent="agent-a", attempt=2):
    return session_fence_matches(
        payload, tenant_id=tenant, agent_id=agent, job_id="job-a",
        attempt_count=attempt, presented_session=session,
    )


def test_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("CAM_AGENT_HARNESS_EXECUTOR_SESSION_FENCE_ENABLED", raising=False)
    assert executor_session_fence_enabled() is False
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXECUTOR_SESSION_FENCE_ENABLED", "true")
    assert executor_session_fence_enabled() is False
    monkeypatch.setenv("CAM_AGENT_HARNESS_EXECUTOR_SESSION_FENCE_ENABLED", "1")
    assert executor_session_fence_enabled() is True


@pytest.mark.parametrize("value", [
    None, "", "00000000-0000-0000-0000-000000000000",
    "c679ba04-3dad-11ef-8c99-0242ac120002",
    "FALSE", "a" * 36, "AAAAAAAA-AAAA-4AAA-AAAA-AAAAAAAAAAAA",
    "6a0b7b0b-604e-4c41-a1d9-98cd86743d2d ", 1,
])
def test_rejects_invalid_noncanonical_session_uuid(value):
    assert normalized_executor_session(value) is None


def test_binding_scoped_to_same_agent_tenant_job_and_attempt():
    session = str(uuid4())
    original = {"source": {"kind": "etsy"}, "execution_mode": "orchestrator_v1"}
    bound = bind_session_fence(
        original, tenant_id="boostgame", agent_id="agent-a", job_id="job-a",
        attempt_count=2, session_id=session,
    )
    assert SESSION_FENCE_FIELD not in original
    assert session not in str(bound)
    assert "source" in bound
    assert _match(bound, session)
    assert not _match(bound, str(uuid4()))
    assert not _match(bound, None)
    assert not _match(bound, session, tenant="another")
    assert not _match(bound, session, agent="other")
    assert not _match(bound, session, attempt=3)
    assert not session_fence_matches(
        bound, tenant_id="boostgame", agent_id="agent-a", job_id="different",
        attempt_count=2, presented_session=session,
    )


def test_malformed_binding_fails_closed_and_rebind_clears_old():
    session = str(uuid4())
    fence = issue_session_fence(
        tenant_id="boostgame", agent_id="agent-a",
        job_id="job-a", attempt_count=2, session_id=session,
    )
    assert not _match({SESSION_FENCE_FIELD: None}, session)
    assert not _match({SESSION_FENCE_FIELD: {}}, session)
    assert not _match({SESSION_FENCE_FIELD: {**fence, "extra": 1}}, session)
    assert not _match({SESSION_FENCE_FIELD: {**fence, "attempt_count": True}}, session)
    assert not _match({SESSION_FENCE_FIELD: {**fence, "session_digest": "bad"}}, session)
    assert _match({"execution_mode": "orchestrator_v1"}, None)  # Legacy unbound.
    next_payload = bind_session_fence(
        {SESSION_FENCE_FIELD: fence}, tenant_id="boostgame",
        agent_id="agent-a", job_id="job-a", attempt_count=3,
        session_id=None,
    )
    assert SESSION_FENCE_FIELD not in next_payload


def test_read_only_session_fence_shape_never_authenticates_or_exposes_uuid():
    owner = str(uuid4())
    fence = issue_session_fence(
        tenant_id="boostgame", agent_id="agent-a", job_id="job-a",
        attempt_count=2, session_id=owner,
    )
    payload = {SESSION_FENCE_FIELD: fence}
    assert session_fence_shape(payload, attempt_count=2) == "structurally_bound"
    assert session_fence_shape({}, attempt_count=2) == "legacy_unbound"
    assert session_fence_shape(None, attempt_count=2) == "legacy_unbound"
    assert session_fence_shape(payload, attempt_count=3) == "attempt_mismatch"
    assert session_fence_shape({SESSION_FENCE_FIELD: None}, attempt_count=2) == "malformed"
    assert session_fence_shape(
        {SESSION_FENCE_FIELD: {**fence, "session_digest": "a"*63}},
        attempt_count=2,
    ) == "malformed"
    assert session_fence_shape(
        {SESSION_FENCE_FIELD: {**fence, "session_digest": "A"*64}},
        attempt_count=2,
    ) == "malformed"
    assert session_fence_shape(
        {SESSION_FENCE_FIELD: {**fence, "attempt_count": True}},
        attempt_count=2,
    ) == "malformed"
    assert session_fence_shape(
        {SESSION_FENCE_FIELD: {**fence, "untrusted": 1}},
        attempt_count=2,
    ) == "malformed"
    assert owner not in str(session_fence_shape(payload, attempt_count=2))
    assert fence["session_digest"] not in str(session_fence_shape(payload, attempt_count=2))


def test_bad_issue_contract_raises_without_writing():
    with pytest.raises(ValueError):
        issue_session_fence(
            tenant_id="boostgame", agent_id="agent-a", job_id="job-a",
            attempt_count=0, session_id=str(uuid4()),
        )
    with pytest.raises(ValueError):
        issue_session_fence(
            tenant_id="boostgame", agent_id="agent-a", job_id="job-a",
            attempt_count=2, session_id="not-a-uuid",
        )
