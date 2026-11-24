"""Server-owned Muse account cooldown and failover routing.

A provider HTTP 429 is reported by the affected, authenticated Muse worker.
Only that worker is paused. Failover never assigns a job to an unverified
runtime and never steals an active video lease. No extra database is created.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Mapping

from .worker_accounts import (
    WORKER_IDS, WorkerAccountConfigError, registration_file,
    worker_execution_readiness,
)

MIN_BACKOFF_SECONDS = 60
MAX_BACKOFF_SECONDS = 86400
STATE_SCHEMA = "CAM_MUSE_ACCOUNT_COOLDOWN_V1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _directory(env: Mapping[str, str]) -> Path:
    directory = registration_file(env).parent / "muse-worker-state"
    try:
        info = directory.lstat()
    except FileNotFoundError as exc:
        raise WorkerAccountConfigError("Muse cooldown state directory not provisioned") from exc
    if (not stat.S_ISDIR(info.st_mode) or directory.is_symlink()
            or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_uid != os.geteuid() and os.geteuid() != 0)):
        raise WorkerAccountConfigError("Muse cooldown directory permissions invalid")
    return directory


def _state_file(directory: Path, worker_id: str) -> Path:
    if worker_id not in WORKER_IDS:
        raise WorkerAccountConfigError("Unknown Muse worker for cooldown")
    return directory / (worker_id + ".json")


def _load_file(file: Path, worker_id: str) -> dict:
    try:
        info = file.lstat()
    except FileNotFoundError:
        return {"schema": STATE_SCHEMA, "workerId": worker_id,
                "cooldownUntilUtc": None, "reason": None}
    if (not stat.S_ISREG(info.st_mode) or file.is_symlink()
            or info.st_size > 4096 or stat.S_IMODE(info.st_mode) != 0o600):
        raise WorkerAccountConfigError("Muse cooldown state file invalid")
    try:
        record = json.loads(file.read_text(encoding="utf-8"))
        if (not isinstance(record, dict)
                or record.get("schema") != STATE_SCHEMA
                or record.get("workerId") != worker_id):
            raise ValueError("bad worker identity")
        until = record.get("cooldownUntilUtc")
        if until is not None:
            parsed = datetime.fromisoformat(until.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone missing")
        return record
    except (TypeError, ValueError, UnicodeError, OSError) as exc:
        raise WorkerAccountConfigError("Muse cooldown state corrupt") from exc


def cooldown_status(env: Mapping[str, str], worker_id: str,
                    *, now: datetime | None = None) -> dict:
    if worker_id == "legacy":
        return {"workerId": worker_id, "cooldownActive": False,
                "cooldownUntilUtc": None, "secondsRemaining": 0}
    current = now or _now()
    if current.tzinfo is None:
        raise ValueError("UTC timestamp required")
    directory = _directory(env)
    record = _load_file(_state_file(directory, worker_id), worker_id)
    raw = record.get("cooldownUntilUtc")
    until = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
    seconds = max(0, math.ceil((until - current).total_seconds())) if until else 0
    return {
        "workerId": worker_id,
        "cooldownActive": seconds > 0,
        "cooldownUntilUtc": until.isoformat() if until else None,
        "secondsRemaining": seconds,
        "reason": record.get("reason") if seconds else None,
    }


def report_provider_cooldown(
    env: Mapping[str, str], worker_id: str, *, provider_status: int,
    retry_after_seconds: int, reason: str = "provider_rate_limit",
    now: datetime | None = None,
) -> dict:
    if (worker_id not in WORKER_IDS or provider_status != 429
            or type(retry_after_seconds) is not int
            or not MIN_BACKOFF_SECONDS <= retry_after_seconds <= MAX_BACKOFF_SECONDS
            or reason not in {"provider_rate_limit", "video_daily_budget_exhausted"}):
        raise ValueError("A valid per-worker provider HTTP 429 and Retry-After are required")
    current = now or _now()
    if current.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    directory = _directory(env)
    lock_path = directory / ".lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        path = _state_file(directory, worker_id)
        prior = _load_file(path, worker_id)
        prior_until = prior.get("cooldownUntilUtc")
        until = current + timedelta(seconds=retry_after_seconds)
        if prior_until:
            existing = datetime.fromisoformat(prior_until.replace("Z", "+00:00"))
            if existing > until:
                until = existing
        data = {
            "schema": STATE_SCHEMA,
            "workerId": worker_id,
            "cooldownUntilUtc": until.isoformat(),
            "reason": reason,
            "providerStatus": 429,
            "updatedAtUtc": current.isoformat(),
        }
        temp = None
        try:
            write_fd, temp = tempfile.mkstemp(prefix="." + worker_id + "-",
                                            suffix=".partial", dir=str(directory))
            os.fchmod(write_fd, 0o600)
            with os.fdopen(write_fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            sync_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(sync_fd)
            finally:
                os.close(sync_fd)
        finally:
            if temp is not None and os.path.exists(temp):
                os.unlink(temp)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return cooldown_status(env, worker_id, now=current)


def failover_status(env: Mapping[str, str],
                    *, now: datetime | None = None) -> dict:
    current = now or _now()
    a = cooldown_status(env, "muse-01", now=current)
    b = cooldown_status(env, "muse-02", now=current)
    eligible_a = worker_execution_readiness(env, "muse-01")["ready"] and not a["cooldownActive"]
    eligible_b = worker_execution_readiness(env, "muse-02")["ready"] and not b["cooldownActive"]
    if eligible_a:
        route = "PRIMARY_READY"
        selected = "muse-01"
    elif eligible_b:
        route = "FAILOVER_TO_MUSE_02"
        selected = "muse-02"
    else:
        route = "WAITING_FOR_AVAILABLE_RENDERER"
        selected = None
    return {
        "mode": route, "preferredWorker": selected,
        "muse01Cooldown": a["cooldownActive"],
        "muse02Cooldown": b["cooldownActive"],
        "muse02Executable": bool(worker_execution_readiness(env, "muse-02")["ready"]),
        "noDuplicateTakeover": True,
    }