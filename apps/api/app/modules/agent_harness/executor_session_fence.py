"""Optional session/attempt fence for the existing Etsy Image Agent lease.

This is a second client-session binding on the *existing* processing_jobs row,
not an independently verified browser-capacity proof, slot reservation, or
authorization to dispatch. There are no new tables, queues or disk artifacts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any
from uuid import UUID

SESSION_FENCE_FIELD = "_cam_harness_executor_session_v1"
_PROTOCOL = "cam-executor-session-fence-v1"
_KEYS = frozenset({"protocol", "attempt_count", "session_digest"})


def executor_session_fence_enabled() -> bool:
    """Default-OFF until the paired Windows extension sends a stable UUID."""
    return os.getenv("CAM_AGENT_HARNESS_EXECUTOR_SESSION_FENCE_ENABLED", "0") == "1"


def normalized_executor_session(value: str | None) -> str | None:
    """Accept only canonical lowercase UUIDv4, never arbitrary browser text."""
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        uuid_value = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return None
    if uuid_value.version != 4 or str(uuid_value) != value:
        return None
    return value


def issue_session_fence(
    *, tenant_id: str, agent_id: str, job_id: str,
    attempt_count: int, session_id: str,
) -> dict[str, Any]:
    """Bind a client UUIDv4 to exactly one tenant, agent, job and attempt."""
    if (normalized_executor_session(session_id) is None
            or type(attempt_count) is not int or attempt_count < 1):
        raise ValueError("invalid_executor_session_contract")
    blob = json.dumps({
        "tenant_id": tenant_id, "agent_id": agent_id, "job_id": job_id,
        "attempt_count": attempt_count, "session_id": session_id,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(
        ("cam-executor-session-fence-v1:" + blob).encode("ascii")
    ).hexdigest()
    return {
        "protocol": _PROTOCOL,
        "attempt_count": attempt_count,
        "session_digest": digest,
    }


def session_fence_matches(
    payload: Any, *, tenant_id: str, agent_id: str, job_id: str,
    attempt_count: int, presented_session: str | None,
) -> bool:
    """Legacy unbound jobs keep working; malformed bound records fail closed.

    The UUID does not replace the dedicated device bearer or per-attempt lease
    token checked by the caller. Its digest never leaves the server.
    """
    if not isinstance(payload, dict) or SESSION_FENCE_FIELD not in payload:
        return True
    observed = payload[SESSION_FENCE_FIELD]
    if not isinstance(observed, dict) or set(observed) != _KEYS:
        return False
    if normalized_executor_session(presented_session) is None:
        return False
    try:
        expected = issue_session_fence(
            tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
            attempt_count=attempt_count, session_id=presented_session,
        )
    except ValueError:
        return False
    return (
        observed.get("protocol") == _PROTOCOL
        and type(observed.get("attempt_count")) is int
        and observed["attempt_count"] == attempt_count
        and isinstance(observed.get("session_digest"), str)
        and hmac.compare_digest(
            observed["session_digest"], expected["session_digest"]
        )
    )


def bind_session_fence(
    payload: Any, *, tenant_id: str, agent_id: str, job_id: str,
    attempt_count: int, session_id: str | None,
) -> dict[str, Any]:
    """Replace old-attempt binding at the same row's existing claim boundary."""
    updated = dict(payload) if isinstance(payload, dict) else {}
    updated.pop(SESSION_FENCE_FIELD, None)
    if session_id is not None:
        updated[SESSION_FENCE_FIELD] = issue_session_fence(
            tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
            attempt_count=attempt_count, session_id=session_id,
        )
    return updated
