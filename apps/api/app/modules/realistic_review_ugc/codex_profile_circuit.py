"""Shared per-profile cooldown to prevent Stage 4 Codex quota/error storms.

No credentials or prompts are persisted. The lock file is shared by all image
worker processes on the same VPS and lives beside that Codex home's slot locks.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

WINDOW_SECONDS = 180
FAILURES_TO_OPEN = 3
TRANSIENT_COOLDOWN_SECONDS = 1800
QUOTA_COOLDOWN_SECONDS = 18000


@contextmanager
def _locked_state(codex_home: str):
    root = Path(codex_home).resolve() / "worker-locks"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "stage4-circuit.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        path = root / "stage4-circuit.json"
        try:
            state = json.loads(path.read_text()) if path.is_file() else {}
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError, TypeError):
            state = {}
        try:
            yield state
            temp = root / "stage4-circuit.tmp"
            temp.write_text(json.dumps(state, separators=(",", ":")))
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def cooldown_remaining(codex_home: str, *, now: float | None = None) -> int:
    clock = time.time() if now is None else now
    with _locked_state(codex_home) as state:
        return max(0, int(float(state.get("until", 0) or 0) - clock))


def note_failure(codex_home: str, code: str, *, now: float | None = None) -> int:
    """After three rapid unexpected provider errors, allow one probe / 30 min."""
    clock = time.time() if now is None else now
    with _locked_state(codex_home) as state:
        start = float(state.get("start", 0) or 0)
        if clock - start > WINDOW_SECONDS or start > clock:
            start, count = clock, 0
        else:
            count = int(state.get("count", 0) or 0)
        count += 1
        state.update(start=start, count=count)
        if code in {"codex_image_usage_limited", "codex_image_auth_required"}:
            state["until"] = max(float(state.get("until", 0) or 0), clock + QUOTA_COOLDOWN_SECONDS)
        elif code in {"codex_image_provider_error", "codex_image_output_missing"} and count >= FAILURES_TO_OPEN:
            state["until"] = max(float(state.get("until", 0) or 0), clock + TRANSIENT_COOLDOWN_SECONDS)
            # A probe failure after cooldown restarts the window.
            state["start"], state["count"] = clock, 0
        return max(0, int(float(state.get("until", 0) or 0) - clock))


def note_success(codex_home: str) -> None:
    with _locked_state(codex_home) as state:
        state.clear()
