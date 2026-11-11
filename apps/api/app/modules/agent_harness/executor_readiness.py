"""Read-only Agent Harness executor preflight; not a dispatcher.

Codex probes reuse the existing RRUGC account-pool state (auth status and
flock slot ownership); no new process runs a model, reserves a slot or creates
a ProcessingJob. Browser Extension is explicitly unverified and fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.browser_image_mode import BROWSER_EXTENSION_READY
from app.modules.realistic_review_ugc.codex_account_manager import (
    PROFILE_IDS, active_slots, auth_status,
)
from app.modules.realistic_review_ugc.model import RrugcCodexAccountConfigModel

from .model import AgentDefinitionModel

# These are the proven RRUGC worker stages, NOT a general coding/shell agent.
_STAGE_CAPABILITIES = {
    "stage1": frozenset({"generate_text", "image_edit"}),
    "stage2": frozenset({"image_edit"}),
    "stage4": frozenset({"image_edit"}),
}
_EXECUTOR_CAPABILITIES = {
    "codex": frozenset({"generate_text", "image_edit"}),
    # Browser is intentionally NOT in this map; it has no verified live bridge.
}


@dataclass(frozen=True)
class ExecutorSnapshot:
    executor_id: str
    status: str
    configured_slots: int
    busy_slots: int
    available_slots: int
    capabilities: frozenset[str]

    def document(self) -> dict[str, Any]:
        return {
            "executor_id": self.executor_id,
            "status": self.status,
            "configured_slots": self.configured_slots,
            "busy_slots": self.busy_slots,
            "available_slots": self.available_slots,
        }

    def verified_advisory_capacity(self, required: frozenset[str]) -> bool:
        """Require coherent, positively verified capacity; never trust slot count alone.

        A browser heartbeat, missing login, stale/unknown probe or malformed
        count is not a reservable slot. Even True is NOT dispatch permission:
        the actual RRUGC worker must still obtain its own authoritative lease.
        """
        return bool(
            self.status == "observed_capacity"
            and all(
                type(value) is int and value >= 0
                for value in (
                    self.configured_slots, self.busy_slots, self.available_slots,
                )
            )
            and self.configured_slots > 0
            and self.available_slots > 0
            and self.busy_slots + self.available_slots == self.configured_slots
            and required.issubset(self.capabilities)
        )


def stage_supports(stage: str, requirements: frozenset[str]) -> bool:
    """Pure guard used BEFORE any potentially slow Codex account probe."""
    return bool(
        requirements
        and stage in _STAGE_CAPABILITIES
        and requirements.issubset(_STAGE_CAPABILITIES[stage])
    )


def probe_codex(
    session: Session, tenant_id: str, stage: str, *,
    codex_home: str, codex_binary: str,
) -> ExecutorSnapshot:
    """Snapshot the existing RRUGC Codex pool without ensure_accounts writes.

    Login status uses the same bounded (<=8s) offline CLI status probe as the
    existing account UI. No credentials, paths, stderr or token enter results.
    A slot snapshot is NOT an allocation and becomes stale immediately.
    """
    if stage not in _STAGE_CAPABILITIES:
        return ExecutorSnapshot("codex", "unsupported_stage", 0, 0, 0, frozenset())
    rows = list(session.scalars(
        select(RrugcCodexAccountConfigModel).where(
            RrugcCodexAccountConfigModel.tenant_id == tenant_id,
            RrugcCodexAccountConfigModel.account_id.in_(PROFILE_IDS),
        )
    ).all())
    candidates = [
        row for row in rows
        if row.enabled and stage in (row.stages_json or []) and row.account_id in PROFILE_IDS
    ][:len(PROFILE_IDS)]
    configured = 0
    busy = 0
    available = 0
    had_unverified = False
    for row in candidates:
        limit = max(1, min(row.max_concurrent, 3))
        # Profile account pool config alone never proves a usable CLI login.
        try:
            state = auth_status(row.account_id, codex_home, codex_binary)
        except (OSError, ValueError):
            state = "check_failed"
        if state != "connected":
            had_unverified = True
            continue
        try:
            active = max(0, min(active_slots(row.account_id, codex_home, limit=limit), limit))
        except (OSError, ValueError):
            had_unverified = True
            continue
        configured += limit
        busy += active
        available += limit - active
    if available:
        state = "observed_capacity"
    elif configured and busy == configured:
        state = "capacity_exhausted"
    elif had_unverified:
        state = "login_not_verified"
    else:
        state = "not_configured"
    return ExecutorSnapshot(
        "codex", state, configured, busy, available,
        _EXECUTOR_CAPABILITIES["codex"],
    )


def browser_snapshot() -> ExecutorSnapshot:
    # No authenticated heartbeat, provider result lineage or live browser
    # response acknowledgment exists in CAM yet; a prototype is NOT healthy.
    return ExecutorSnapshot(
        "browser",
        "provider_unverified" if not BROWSER_EXTENSION_READY else "heartbeat_unavailable",
        0, 0, 0, frozenset(),
    )


def preview_route(
    agent: AgentDefinitionModel,
    *,
    stage: str,
    required_capabilities: frozenset[str],
    required_tools: frozenset[str],
    codex: ExecutorSnapshot,
    browser: ExecutorSnapshot,
    global_emergency_stop: bool,
) -> dict[str, Any]:
    """Bounded, explainable preflight only. NEVER returns authorization to run.

    This can inform future queue integration, but must not be used to claim
    ownership of a slot or route new jobs.
    """
    observations = {"codex": codex, "browser": browser}
    result = {
        "tenant_id": agent.tenant_id,
        "agent_id": agent.agent_id,
        "version": agent.version,
        "stage": stage,
        "dispatch_allowed": False,
        "execution_enabled": False,
        "candidate_executor": None,
        "candidate_priority": None,
        "advisory_agent_slot_limit": 0,
        "reason": "blocked",
        "executor_observations": [observations[key].document() for key in ("codex", "browser")],
        "slot_counts_are_advisory": True,
    }
    if not required_capabilities or not required_capabilities.issubset(set(agent.capabilities_json)):
        result["reason"] = "capability_not_permitted"
        return result
    if not required_tools.issubset(set(agent.allowed_tools_json)):
        result["reason"] = "tool_not_permitted"
        return result
    if not stage_supports(stage, required_capabilities):
        result["reason"] = "stage_capability_unsupported"
        return result
    if global_emergency_stop:
        result["reason"] = "emergency_stop"
        return result
    if not agent.enabled:
        # Immutable definitions are always inserted disabled in Phase 2C.
        result["reason"] = "agent_inactive"
        # Continue to calculate advisory capacity only if permitted.
    for priority, executor_id in enumerate(
        (agent.preferred_executor, *(agent.fallback_executors_json or []))
    ):
        candidate = observations.get(executor_id)
        if candidate is not None and candidate.verified_advisory_capacity(required_capabilities):
            result["candidate_executor"] = executor_id
            result["candidate_priority"] = "preferred" if priority == 0 else "fallback"
            # Only an upper bound; no slot is reserved, no queue lease exists.
            result["advisory_agent_slot_limit"] = min(
                agent.max_concurrent, candidate.available_slots,
            )
            break
    if result["candidate_executor"] is None:
        result["reason"] = "no_verified_executor_capacity"
    elif not agent.enabled:
        result["reason"] = "agent_inactive"
    else:
        result["reason"] = "execution_flag_disabled"
    return result
