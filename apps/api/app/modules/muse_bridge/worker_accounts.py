"""Optional two-account Muse protocol. Disabled by default; no quotas bypassed.

Every account authenticates with its own random bearer secret. CAM stores only
SHA-256 fingerprints supplied through its protected environment. Separate
worker cooldowns never pause or advance the other account's local budget.

No new DB instance, no renderer and no automatic login session duplication.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

WORKER_IDS = ("muse-01", "muse-02")
PROTOCOL = "CAM_MUSE_DUAL_WORKER_V1"
POLICY_VERSION = "2026-11-23.dual-worker.1"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]{48,128})\Z")


class WorkerAccountConfigError(ValueError):
    pass


@dataclass(frozen=True)
class WorkerIdentity:
    id: str
    legacy: bool = False


REGISTRY_FILE_NAME = "muse-worker-registration-v1.json"


def registration_file(environ: Mapping[str, str]) -> Path:
    """Find the protected project ops registry across release symlink switches."""
    explicit = environ.get("CAM_MUSE_WORKER_REGISTRATION_FILE")
    if explicit:
        return Path(explicit)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "ops" / REGISTRY_FILE_NAME
        if candidate.is_file():
            return candidate
    # No registry at all is legacy mode; a malformed one fails closed.
    return Path.cwd() / "ops" / REGISTRY_FILE_NAME


def _validated_roster(obj: object) -> dict[str, str]:
    if not isinstance(obj, dict) or set(obj) != set(WORKER_IDS):
        raise WorkerAccountConfigError("Exactly two configured worker IDs required")
    if any(not isinstance(v, str) or not HEX64.fullmatch(v) for v in obj.values()):
        raise WorkerAccountConfigError("Invalid worker token hash")
    if len(set(obj.values())) != 2:
        raise WorkerAccountConfigError("Worker tokens must be distinct")
    return dict(obj)


def read_registration(environ: Mapping[str, str]) -> dict | None:
    """Use a root-controlled 0600 registry, without saving any raw token."""
    path = registration_file(environ)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not path.is_file() or path.is_symlink()
            or info.st_size < 50 or info.st_size > 4096
            or (info.st_mode & 0o077)):
        raise WorkerAccountConfigError("Muse worker registry permissions invalid")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise WorkerAccountConfigError("Invalid Muse worker registry") from exc
    if (not isinstance(raw, dict)
            or raw.get("schema") != "CAM_MUSE_WORKER_REGISTRATION_V1"
            or not isinstance(raw.get("workers"), dict)):
        raise WorkerAccountConfigError("Invalid Muse worker registry schema")
    return raw


def worker_execution_readiness(
    environ: Mapping[str, str], worker_id: str
) -> dict[str, object]:
    """Operator-verified runtime capability, separate from authentication.

    Identity/authentication alone is insufficient to reserve a CAM command.
    Keep working Muse-01 available; new Muse-02 is fail-closed until its
    independent persistent runner and authorized renderer are both proven.
    The private registry is the single operator-owned source of truth.
    """
    if worker_id == "legacy":
        return {"ready": True, "persistentRunnerVerified": True,
                "rendererVerified": False, "reason": None}
    if worker_id == "muse-01":
        return {"ready": True, "persistentRunnerVerified": True,
                "rendererVerified": False, "reason": None}
    if worker_id != "muse-02":
        return {"ready": False, "persistentRunnerVerified": False,
                "rendererVerified": False, "reason": "unknown_worker"}
    registered = read_registration(environ)
    member = (registered.get("workers", {}).get(worker_id)
              if isinstance(registered, dict) else None)
    if not isinstance(member, dict):
        return {"ready": False, "persistentRunnerVerified": False,
                "rendererVerified": False, "reason": "worker_not_registered"}
    persistent = member.get("persistentRunnerVerified") is True
    renderer = member.get("rendererVerified") is True
    return {"ready": persistent and renderer,
            "persistentRunnerVerified": persistent,
            "rendererVerified": renderer,
            "scheduledRendererSelfTestVerified":
                member.get("scheduledRendererSelfTestVerified") is True,
            "camOutputContractVerified":
                member.get("canaryOutputContractVerified") is True,
            "camUploadReceiptVerified":
                member.get("canaryUploadReceiptVerified") is True,
            "rendererVerificationScope":
                member.get("rendererVerificationScope"),
            "reason": None if persistent and renderer else
            "worker_runtime_or_renderer_not_verified"}


def legacy_claims_draining(environ: Mapping[str, str]) -> bool:
    """Stop NEW legacy claims, not heartbeat/finish, before swapping ownership."""
    record = read_registration(environ)
    if record is None:
        return False
    state = record.get("activation")
    return isinstance(state, dict) and state.get("drainLegacyClaims") is True


def load_roster(environ: Mapping[str, str]) -> dict[str, str] | None:
    """Dual account roster, from env or the protected persistent CAM registry.

    A scoped operator action can activate the stable 0600 registry when systemd
    environment files are not writable from the authorized CAM workspace.
    No raw Bearer tokens are saved, and flag OFF preserves legacy behavior.
    """
    if environ.get("CAM_MUSE_DUAL_WORKERS_ENABLED", "false").lower() == "true":
        try:
            roster = json.loads(environ.get("CAM_MUSE_WORKER_TOKEN_HASHES_JSON", ""))
        except (TypeError, ValueError) as exc:
            raise WorkerAccountConfigError("Invalid dual Muse worker roster") from exc
        return _validated_roster(roster)
    record = read_registration(environ)
    state = record.get("activation") if record else None
    if not isinstance(state, dict) or state.get("enabled") is not True:
        return None
    if state.get("protocol") != PROTOCOL or state.get("policyVersion") != POLICY_VERSION:
        raise WorkerAccountConfigError("Muse registry policy version mismatched")
    members = record["workers"]
    if set(members) != set(WORKER_IDS) or any(
        not isinstance(members.get(i), dict) or members[i].get("status") != "active"
        for i in WORKER_IDS
    ):
        raise WorkerAccountConfigError("Muse registry worker activation incomplete")
    return _validated_roster({i: members[i].get("token_sha256") for i in WORKER_IDS})


def authorize_worker(
    authorization: str | None, *, environ: Mapping[str, str],
    legacy_token: str
) -> WorkerIdentity | None:
    m = BEARER.fullmatch(authorization or "")
    if not m:
        return None
    candidate_hash = hashlib.sha256(m.group(1).encode("ascii")).hexdigest()
    roster = load_roster(environ)
    if roster is None:
        valid = hmac.compare_digest(candidate_hash,
                                    hashlib.sha256(legacy_token.encode()).hexdigest())
        return WorkerIdentity("legacy", True) if valid else None
    selected = None
    # Both comparisons run regardless of match to avoid short-circuiting.
    for worker_id in WORKER_IDS:
        if hmac.compare_digest(candidate_hash, roster[worker_id]):
            selected = worker_id
    return WorkerIdentity(selected) if selected else None


def authorize_preregistered_worker(
    authorization: str | None, *, registry_file: str | Path
) -> WorkerIdentity | None:
    """Authenticate a pending Muse-02 ONLY for read-only config discovery.

    Pending registration does not authorize command/job claim routes. Those
    continue to require an active dual roster in production.
    """
    match = BEARER.fullmatch(authorization or "")
    if match is None:
        return None
    path = Path(registry_file)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    if not path.is_file() or stat.st_size < 10 or stat.st_size > 4096:
        raise WorkerAccountConfigError("Invalid pending worker registry")
    if stat.st_mode & 0o007:
        raise WorkerAccountConfigError("Pending worker registry is world accessible")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError, OSError) as exc:
        raise WorkerAccountConfigError("Malformed pending worker registry") from exc
    workers = data.get("workers") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("schema") != "CAM_MUSE_WORKER_REGISTRATION_V1" or not isinstance(workers, dict):
        raise WorkerAccountConfigError("Invalid pending worker schema")
    entry = workers.get("muse-02")
    if not isinstance(entry, dict) or entry.get("status") != "pending_activation":
        return None
    expected = entry.get("token_sha256")
    if not isinstance(expected, str) or not HEX64.fullmatch(expected):
        raise WorkerAccountConfigError("Invalid Muse-02 credential fingerprint")
    actual = hashlib.sha256(match.group(1).encode("ascii")).hexdigest()
    return WorkerIdentity("muse-02") if hmac.compare_digest(actual, expected) else None


def verify_lease_owner(details: dict | None, worker_id: str, *, enabled: bool) -> bool:
    """Legacy leases remain valid only when dual account rollout is disabled."""
    if not enabled:
        return True
    return (isinstance(details, dict)
            and details.get("museWorkerOwner") == worker_id
            and worker_id in WORKER_IDS)


def worker_config(identity: WorkerIdentity, *, v33_enabled: bool,
                  v33_pilots: int) -> dict:
    return {
        "protocol": PROTOCOL,
        "policyVersion": POLICY_VERSION,
        "workerId": identity.id,
        "workerCount": 1 if identity.legacy else 2,
        "dualEnabled": not identity.legacy,
        "pollPath": "/api/v1/muse-bridge/commands/wait?waitSeconds=20&limit=1",
        "workPath": "/api/v1/muse-bridge/video-work?limit=3",
        "claimPolicy": "one_command_one_worker_atomic_lease",
        "leaseSeconds": 1800,
        "maxConcurrentClaimsPerWorker": 1,
        "cooldownScope": "authenticated_worker_account",
        "cooldownPolicy": "stop_claiming_on_429_respect_provider_retry_after",
        "cooldownReportPath": "/api/v1/muse-bridge/workers/cooldown",
        "cooldownReportProviderStatus": 429,
        "yieldUnstartedCommandPath": "/api/v1/muse-bridge/commands/{commandId}/yield",
        "cooldownRequiresProviderBackoff": True,
        "failoverRevision": "2026-11-23.cooldown-failover.1",
        "failoverPreference": ["muse-01", "muse-02"],
        "preserveClaimedVideoLeaseOnCooldown": True,
        "pollDuringCooldown": False,
        "v33Enabled": v33_enabled,
        "v33PilotCount": v33_pilots,
        "v33RequiresGenuineTwoTake": True,
        "targetDurationSeconds": 12,
        "canGenerateVideo": False,  # Bridge cannot render; Muse client must do so.
        "updates": "both workers fetch fresh config; never copy login sessions",
        "artifactPolicy": "SHA256_verified_immutable_per_aspect",
    }


@dataclass(frozen=True)
class CooldownState:
    worker_id: str
    cooldown_until: datetime | None = None
    reason: str | None = None

    def may_claim(self, now: datetime) -> bool:
        return self.cooldown_until is None or now >= self.cooldown_until


def record_cooldown(
    state: CooldownState, *, status_code: int, retry_after_seconds: int,
    now: datetime, reason: str = "provider_quota"
) -> CooldownState:
    """Independent account throttle; never alters a peer's state or limits."""
    if state.worker_id not in WORKER_IDS or status_code != 429:
        raise ValueError("Cooldown requires a real per-account HTTP 429")
    if not 60 <= retry_after_seconds <= 86400:
        raise ValueError("Retry-After must be a bounded documented interval")
    if now.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    until = now + timedelta(seconds=retry_after_seconds)
    if state.cooldown_until and state.cooldown_until > until:
        until = state.cooldown_until
    return CooldownState(state.worker_id, until, reason)


def load_cooldown_state(file: str | Path, *, worker_id: str) -> CooldownState:
    """Read only THIS Muse account's durable quota state after a restart."""
    if worker_id not in WORKER_IDS:
        raise WorkerAccountConfigError("Unknown Muse worker ID")
    path = Path(file)
    if not path.exists():
        return CooldownState(worker_id)
    if not path.is_file() or path.stat().st_size > 4096:
        raise WorkerAccountConfigError("Muse cooldown state is invalid")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw["schema"] != "CAM_MUSE_COOLDOWN_V1" or raw["workerId"] != worker_id:
            raise ValueError("Mismatched account")
        until = (datetime.fromisoformat(raw["cooldownUntil"])
                 if raw.get("cooldownUntil") else None)
        if until is not None and until.tzinfo is None:
            raise ValueError("Timezone missing")
        return CooldownState(worker_id, until, raw.get("reason"))
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise WorkerAccountConfigError(
            "Bad Muse cooldown state; do not automatically claim jobs"
        ) from exc


def save_cooldown_state(file: str | Path, state: CooldownState) -> None:
    """No tokens, cookies or model data are ever serialized."""
    if state.worker_id not in WORKER_IDS:
        raise WorkerAccountConfigError("Unknown Muse worker ID")
    path = Path(file)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "schema": "CAM_MUSE_COOLDOWN_V1",
        "workerId": state.worker_id,
        "cooldownUntil": (state.cooldown_until.isoformat()
                          if state.cooldown_until else None),
        "reason": state.reason,
    }
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".partial")
    try:
        tmp.write_text(json.dumps(content, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)