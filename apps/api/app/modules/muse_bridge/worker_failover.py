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


def _validate_directory(directory: Path) -> Path:
    try:
        info = directory.lstat()
    except OSError as exc:
        raise WorkerAccountConfigError("Muse cooldown state directory unavailable") from exc
    if (not stat.S_ISDIR(info.st_mode) or directory.is_symlink()
            or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_uid != os.geteuid() and os.geteuid() != 0)):
        raise WorkerAccountConfigError("Muse cooldown directory permissions invalid")
    return directory


def _legacy_directory(env: Mapping[str, str]) -> Path:
    return registration_file(env).parent / "muse-worker-state"


def _directory(env: Mapping[str, str]) -> Path:
    # systemd already provisions a writable, persistent StateDirectory for
    # CAM. Keep the registration/configuration tree read-only. Standalone
    # tools may explicitly select the same state directory; tests and legacy
    # installations without systemd retain their existing directory.
    explicit = env.get("CAM_MUSE_COOLDOWN_STATE_DIR")
    root = env.get("STATE_DIRECTORY")
    if not explicit and not root:
        return _validate_directory(_legacy_directory(env))
    selected = Path(explicit) if explicit else Path(root) / "muse-worker-state"
    if not selected.is_absolute() or ":" in str(selected) or ".." in selected.parts:
        raise WorkerAccountConfigError("Invalid Muse persistent state directory")
    try:
        parent = selected.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or selected.parent.is_symlink()
                or (parent.st_uid != os.geteuid() and os.geteuid() != 0)):
            raise WorkerAccountConfigError("Muse persistent state root invalid")
        selected.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise WorkerAccountConfigError("Muse persistent state directory unavailable") from exc
    return _validate_directory(selected)


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
    except OSError as exc:
        raise WorkerAccountConfigError("Muse cooldown state unreadable") from exc
    if (not stat.S_ISREG(info.st_mode) or file.is_symlink()
            or info.st_size > 4096 or stat.S_IMODE(info.st_mode) != 0o600
            or (info.st_uid != os.geteuid() and os.geteuid() != 0)):
        raise WorkerAccountConfigError("Muse cooldown state file invalid")
    try:
        record = json.loads(file.read_text(encoding="utf-8"))
        if (not isinstance(record, dict)
                or record.get("schema") != STATE_SCHEMA
                or record.get("workerId") != worker_id):
            raise ValueError("bad worker identity")
        until = record.get("cooldownUntilUtc")
        if until is not None:
            if (not isinstance(until, str) or record.get("reason") not in
                    ("provider_rate_limit", "video_daily_budget_exhausted")):
                raise ValueError("invalid cooldown record")
            parsed = datetime.fromisoformat(until.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone missing")
        return record
    except (TypeError, ValueError, UnicodeError, OSError) as exc:
        raise WorkerAccountConfigError("Muse cooldown state corrupt") from exc


def _until(record: dict) -> datetime | None:
    raw = record.get("cooldownUntilUtc")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


def _current_record(env, directory, worker_id):
    """Preserve legacy cooldowns across the storage-location upgrade.

    The old state directory is read-only; never overwrite or remove it.
    Choose the later recorded deadline rather than resetting an active hold.
    """
    primary = _load_file(_state_file(directory, worker_id), worker_id)
    legacy = _legacy_directory(env)
    if legacy == directory:
        return primary
    try:
        legacy.lstat()
    except FileNotFoundError:
        return primary
    except OSError as exc:
        raise WorkerAccountConfigError("Legacy Muse cooldown unavailable") from exc
    previous = _load_file(_state_file(_validate_directory(legacy), worker_id), worker_id)
    old_until, new_until = _until(previous), _until(primary)
    return previous if old_until and (not new_until or old_until > new_until) else primary


def cooldown_status(env: Mapping[str, str], worker_id: str,
                    *, now: datetime | None = None) -> dict:
    if worker_id == "legacy":
        return {"workerId": worker_id, "cooldownActive": False,
                "cooldownUntilUtc": None, "secondsRemaining": 0}
    current = now or _now()
    if current.tzinfo is None:
        raise ValueError("UTC timestamp required")
    directory = _directory(env)
    record = _current_record(env, directory, worker_id)
    until = _until(record)
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
    now: datetime | None = None, cooldown_until: datetime | None = None,
) -> dict:
    if (worker_id not in WORKER_IDS or provider_status != 429
            or type(retry_after_seconds) is not int
            or not MIN_BACKOFF_SECONDS <= retry_after_seconds <= MAX_BACKOFF_SECONDS
            or reason not in {"provider_rate_limit", "video_daily_budget_exhausted"}):
        raise ValueError("A valid per-worker provider HTTP 429 and Retry-After are required")
    current = now or _now()
    if current.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    if cooldown_until is not None:
        if (cooldown_until.tzinfo is None or
                cooldown_until > current + timedelta(seconds=MAX_BACKOFF_SECONDS)):
            raise ValueError("Invalid absolute provider cooldown deadline")
        if cooldown_until <= current:
            # A delayed replay must not reopen or extend an expired hold.
            return cooldown_status(env, worker_id, now=current)
    until = cooldown_until or current + timedelta(seconds=retry_after_seconds)
    directory = _directory(env)
    lock_path = directory / ".lock"
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or (info.st_uid != os.geteuid() and os.geteuid() != 0)):
                raise WorkerAccountConfigError("Muse cooldown lock invalid")
            fcntl.flock(fd, fcntl.LOCK_EX)
            path = _state_file(directory, worker_id)
            prior = _current_record(env, directory, worker_id)
            existing = _until(prior)
            replay_relative_daily = (
                cooldown_until is None and existing is not None and existing > current
                and reason == prior.get("reason") == "video_daily_budget_exhausted")
            if existing and (existing >= until or replay_relative_daily):
                # Do not turn a cron replay of the daily 429 into a sliding
                # cooldown. A genuine later provider deadline must be explicit.
                # Keep the longer already recorded hold and its reason.
                until = existing
                reason = prior.get("reason") or reason
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
                with os.fdopen(write_fd, "w", encoding="utf-8") as stream:
                    os.fchmod(stream.fileno(), 0o600)
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
            os.close(fd)
    except OSError as exc:
        # The API converts this to 503; never pretend a failed write was saved.
        raise WorkerAccountConfigError("Muse cooldown persistence unavailable") from exc
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