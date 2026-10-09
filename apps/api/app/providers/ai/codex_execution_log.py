"""Bounded, tenant-safe Codex CLI progress logs (no model text or prompts)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")
_SAFE_EVENT = re.compile(r"[^a-zA-Z0-9_.-]")
_CAPTURE_BYTES = 65536
_MAX_EVENTS_BYTES = 98304
_EVENT_LINE_BYTES = 32768
_RETENTION_SECONDS = 5 * 86400


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class _BoundedCapture:
    """First and last 64 KiB only, regardless of CLI output volume."""
    def __init__(self) -> None:
        self.first = bytearray()
        self.last = bytearray()
        self.total_bytes = 0

    def append(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        if len(self.first) < _CAPTURE_BYTES:
            self.first.extend(chunk[: _CAPTURE_BYTES - len(self.first)])
        self.last = (self.last + chunk)[-_CAPTURE_BYTES:]

    def text(self) -> str:
        first = bytes(self.first)
        last = bytes(self.last)
        return (first if self.total_bytes <= _CAPTURE_BYTES else
                first + b"\n... [intermediate output omitted] ...\n" + last).decode(
                    "utf-8", errors="replace")


class CodexExecutionLog:
    def __init__(self, staging_root: str, execution_id: str | None) -> None:
        self.enabled = bool(execution_id and _SAFE_ID.fullmatch(execution_id))
        self.start = time.monotonic()
        self.started_at = _utc()
        self.last_activity_at = self.started_at
        self.last_event = "started"
        self.event_count = 0
        self.stdout_bytes = 0
        self.stderr_bytes = 0
        self._written = 0
        self._last_snapshot = 0.0
        self._log = None
        self.status_file: Path | None = None
        if not self.enabled:
            return
        base = Path(staging_root).resolve() / "codex-execution-logs"
        try:
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(base, 0o700)
            self.status_file = base / (execution_id + ".json")
            self._log = (base / (execution_id + ".jsonl")).open("w", encoding="utf-8", buffering=1)
            os.chmod(self._log.name, 0o600)
            self.snapshot("running", force=True)
        except OSError:
            # Logging is best-effort: a temporarily unwritable disk must not
            # turn an otherwise healthy image-generation attempt into failure.
            self.enabled = False
            self.status_file = None
            if self._log is not None:
                self._log.close()
                self._log = None

    def _append_event(self, kind: str, item: str = "") -> None:
        kind = _SAFE_EVENT.sub("", kind)[:65] or "event"
        item = _SAFE_EVENT.sub("", item)[:65]
        self.last_event = kind + (":" + item if item else "")
        self.last_activity_at = _utc()
        self.event_count += 1
        if self._log is not None:
            line = json.dumps({"time": self.last_activity_at, "event": kind, "item": item},
                              separators=(",", ":")) + "\n"
            if self._written + len(line.encode("utf-8")) <= _MAX_EVENTS_BYTES:
                try:
                    self._log.write(line)
                    self._written += len(line.encode("utf-8"))
                except OSError:
                    self._log.close()
                    self._log = None
        self.snapshot("running")

    def snapshot(self, state: str, *, force: bool = False) -> None:
        if self.status_file is None:
            return
        now = time.monotonic()
        if not force and now - self._last_snapshot < 5:
            return
        self._last_snapshot = now
        payload = {
            "state": state, "started_at": self.started_at,
            "last_activity_at": self.last_activity_at,
            "elapsed_seconds": round(now - self.start),
            "event_count": self.event_count, "last_event": self.last_event,
            "stdout_bytes": self.stdout_bytes, "stderr_bytes": self.stderr_bytes,
        }
        temporary = self.status_file.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.status_file)
        except OSError:
            self.status_file = None
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def close(self, state: str) -> None:
        self.snapshot(state, force=True)
        if self._log is not None:
            self._log.close()
            self._log = None


def _add_stdout_events(log: CodexExecutionLog, raw: bytearray, data: bytes) -> None:
    raw.extend(data)
    while True:
        end = raw.find(b"\n")
        if end < 0:
            if len(raw) > _EVENT_LINE_BYTES:
                # A single huge JSON event may contain model text or image
                # metadata. Drop its contents; keep only its output byte count.
                raw.clear()
            break
        line = bytes(raw[:end])
        del raw[:end + 1]
        if len(line) > _EVENT_LINE_BYTES:
            continue
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict):
            item = event.get("item")
            log._append_event(str(event.get("type") or "event"),
                              str(item.get("type") or "") if isinstance(item, dict) else "")


async def stream_codex_process(process, *, timeout_seconds: int,
                               log: CodexExecutionLog) -> tuple[str, str]:
    """Drain both pipes incrementally; never materialize unbounded process output."""
    stdout_capture = _BoundedCapture()
    stderr_capture = _BoundedCapture()

    async def read_pipe(reader, capture: _BoundedCapture, *, stdout: bool) -> None:
        pending = bytearray()
        while chunk := await reader.read(32768):
            capture.append(chunk)
            if stdout:
                log.stdout_bytes = capture.total_bytes
                _add_stdout_events(log, pending, chunk)
            else:
                log.stderr_bytes = capture.total_bytes
                log.last_activity_at = _utc()
                log.snapshot("running")
        if stdout and pending:
            _add_stdout_events(log, pending, b"\n")

    async def monitor() -> None:
        await asyncio.gather(
            read_pipe(process.stdout, stdout_capture, stdout=True),
            read_pipe(process.stderr, stderr_capture, stdout=False),
            process.wait(),
        )
    try:
        await asyncio.wait_for(monitor(), timeout=max(30, int(timeout_seconds)))
    except asyncio.TimeoutError:
        log.close("timeout")
        raise
    except BaseException:
        log.close("interrupted")
        raise
    log.close("cli_finished" if process.returncode == 0 else "cli_failed")
    return stdout_capture.text(), stderr_capture.text()


def read_codex_execution_log(staging_root: str, execution_id: str) -> dict | None:
    if not _SAFE_ID.fullmatch(execution_id):
        return None
    base = Path(staging_root).resolve() / "codex-execution-logs"
    status_file = base / (execution_id + ".json")
    events_file = base / (execution_id + ".jsonl")
    try:
        if not status_file.is_file():
            return None
        status = json.loads(status_file.read_text(encoding="utf-8"))
        if not isinstance(status, dict):
            return None
        allowed = ("state", "started_at", "last_activity_at", "elapsed_seconds",
                   "event_count", "last_event", "stdout_bytes", "stderr_bytes")
        result = {key: status.get(key) for key in allowed}
        if result.get("state") == "running":
            try:
                started = datetime.fromisoformat(str(result["started_at"]))
                elapsed = int((datetime.now(timezone.utc) - started).total_seconds())
                result["elapsed_seconds"] = max(int(result.get("elapsed_seconds") or 0), max(0, elapsed))
            except (ValueError, TypeError):
                pass
        if events_file.is_file():
            # On-disk logs are bounded at 96 KiB; expose only the latest 24
            # metadata events, never full CLI text/prompt/tool contents.
            lines = events_file.read_text(encoding="utf-8").splitlines()[-24:]
            result["events"] = [json.loads(line) for line in lines]
        else:
            result["events"] = []
        return result
    except (OSError, ValueError, TypeError):
        return None


def prune_codex_execution_logs(staging_root: str, *, max_entries: int = 400) -> None:
    """Bound disk retention without blocking a job on large directory scans."""
    root = Path(staging_root).resolve() / "codex-execution-logs"
    if not root.is_dir():
        return
    cutoff = time.time() - _RETENTION_SECONDS
    try:
        for index, item in enumerate(root.iterdir()):
            if index >= max_entries:
                break
            if item.is_file() and item.suffix in {".json", ".jsonl"} and item.stat().st_mtime < cutoff:
                item.unlink(missing_ok=True)
    except OSError:
        pass
