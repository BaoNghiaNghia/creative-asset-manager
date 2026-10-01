from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ERROR_RETENTION_DAYS = 10
MAX_ARCHIVED_MESSAGE_CHARS = 40_000
_ARCHIVE_NAME = re.compile(r"^errors-(\d{4}-\d{2}-\d{2})\.jsonl$")
_SECRET_VALUE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|"
    r"client[_-]?secret|password|secret)\b"
    r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_PLAIN_ERROR = re.compile(
    r"(?i)(^|\b)(error|critical|fatal|panic|exception|traceback|"
    r"failed|failure|out of memory|oom|killed process|"
    r"connecttimeout|readtimeout|connectionerror)(\b|:)"
)
_ERROR_CODE = re.compile(
    r'(?i)[\"\']?error_code[\"\']?\s*[:=]\s*[\"\']?'
    r'(?!none\b|null\b|ok\b|success\b|0\b)([A-Za-z0-9_.:-]+)'
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _redact_url(match: re.Match[str]) -> str:
    value = match.group(0)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "[redacted-url]"


def redact_error_text(value: str) -> str:
    """Redact common secrets and URL query strings from an archived log line."""
    text = _URL.sub(_redact_url, value)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", text)
    if len(text) > MAX_ARCHIVED_MESSAGE_CHARS:
        return text[:MAX_ARCHIVED_MESSAGE_CHARS] + "…[truncated]"
    return text


def _parse_structured(message: str) -> dict[str, Any] | None:
    stripped = message.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def classify_error_message(message: str) -> tuple[bool, str]:
    """Return whether a journal message is operationally relevant and its severity."""
    if message.startswith("error-logger:"):
        return False, "error"

    lifecycle = message.strip()
    if re.match(
        r"^(Starting|Started|Stopping|Stopped|Finished) "
        r"creative-asset-manager-[A-Za-z0-9_.@-]+\.(service|timer)\b",
        lifecycle,
    ):
        return False, "error"
    if re.match(
        r"^creative-asset-manager-[A-Za-z0-9_.@-]+\.(service|timer): "
        r"(Deactivated successfully\.|Consumed .+ CPU time)",
        lifecycle,
    ):
        return False, "error"

    structured = _parse_structured(message)
    if structured is not None:
        level = str(structured.get("level") or "").strip().upper()
        if level in {"CRITICAL", "FATAL"}:
            return True, "critical"
        if level == "ERROR":
            return True, "error"
        if structured.get("exception"):
            return True, "error"

        outcome = str(
            structured.get("final_outcome")
            or structured.get("outcome")
            or structured.get("status")
            or ""
        ).strip().lower()
        error_code = str(structured.get("error_code") or "").strip().lower()
        if error_code and error_code not in {"none", "null", "ok", "success", "0"}:
            if outcome in {"deferred", "retry_scheduled", "retrying", "busy"}:
                return True, "warning"
            return True, "error"
        if outcome in {"error", "failed", "failure", "terminal_failure", "retryable_failure"}:
            return True, "error"
        if outcome in {"deferred", "retry_scheduled", "retrying"}:
            return True, "warning"

    normalized = message.replace('\\\"', '"')
    if message.lstrip().upper().startswith("WARNING:") and not re.search(
        r"(?i)\b(error|critical|fatal|exception|traceback|timeout|oom|out of memory)\b",
        message,
    ):
        return False, "error"
    if _ERROR_CODE.search(normalized):
        return True, "error"
    if _PLAIN_ERROR.search(message):
        severity = "critical" if re.search(r"(?i)\b(critical|fatal|panic|oom|out of memory)\b", message) else "error"
        return True, severity
    return False, "error"


def journal_service(record: dict[str, Any]) -> str | None:
    """Resolve the CAM service for both service output and systemd manager lines."""
    for key in ("UNIT", "_SYSTEMD_UNIT"):
        value = str(record.get(key) or "").strip()
        if value.startswith("creative-asset-manager-"):
            return value
    return None


def record_timestamp(record: dict[str, Any]) -> datetime:
    raw = record.get("__REALTIME_TIMESTAMP")
    try:
        micros = int(str(raw))
    except (TypeError, ValueError):
        return utcnow()
    return datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc)


def normalize_journal_error(record: dict[str, Any]) -> dict[str, Any] | None:
    service = journal_service(record)
    if not service:
        return None
    message = str(record.get("MESSAGE") or "")
    is_error, severity = classify_error_message(message)
    if not is_error:
        return None
    timestamp = record_timestamp(record)
    return {
        "timestamp": timestamp.isoformat(),
        "severity": severity,
        "service": service,
        "message": redact_error_text(message),
        "journal_cursor": str(record.get("__CURSOR") or "") or None,
        "pid": str(record.get("_PID") or "") or None,
        "process": str(record.get("SYSLOG_IDENTIFIER") or record.get("_COMM") or "") or None,
        "boot_id": str(record.get("_BOOT_ID") or "") or None,
        "source": "journald",
    }


def archive_path(archive_dir: Path, timestamp: datetime) -> Path:
    return archive_dir / f"errors-{timestamp.astimezone(timezone.utc).date().isoformat()}.jsonl"


def append_archived_errors(archive_dir: Path, rows: Iterable[dict[str, Any]]) -> int:
    archive_dir.mkdir(parents=True, exist_ok=True)
    handles: dict[Path, Any] = {}
    written = 0
    try:
        for row in rows:
            timestamp = datetime.fromisoformat(str(row["timestamp"]))
            path = archive_path(archive_dir, timestamp)
            handle = handles.get(path)
            if handle is None:
                handle = path.open("a", encoding="utf-8")
                handles[path] = handle
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            written += 1
        for handle in handles.values():
            handle.flush()
    finally:
        for handle in handles.values():
            handle.close()
    return written


def purge_expired_archives(
    archive_dir: Path,
    *,
    now: datetime | None = None,
    retention_days: int = ERROR_RETENTION_DAYS,
) -> int:
    moment = now or utcnow()
    cutoff = moment - timedelta(days=retention_days)
    cutoff_date = cutoff.date()
    removed = 0
    if not archive_dir.exists():
        return removed
    for path in archive_dir.iterdir():
        match = _ARCHIVE_NAME.match(path.name)
        if not match:
            continue
        try:
            file_date = datetime.fromisoformat(match.group(1)).date()
        except ValueError:
            continue
        if file_date < cutoff_date:
            path.unlink(missing_ok=True)
            removed += 1
            continue
        if file_date != cutoff_date:
            continue

        # The boundary file can contain records just over the exact 10-day
        # window. Rewrite only this one file so retention never exceeds 10 days.
        kept: list[str] = []
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                    timestamp = datetime.fromisoformat(str(row["timestamp"]))
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
                if timestamp >= cutoff:
                    kept.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        if not kept:
            path.unlink(missing_ok=True)
            removed += 1
            continue
        temporary = path.with_suffix(".jsonl.new")
        temporary.write_text("".join(kept), encoding="utf-8")
        temporary.replace(path)
    return removed


def iter_archived_errors(
    archive_dir: Path,
    *,
    since: datetime,
    until: datetime | None = None,
) -> Iterable[dict[str, Any]]:
    if not archive_dir.exists():
        return
    end = until or utcnow()
    for path in sorted(archive_dir.glob("errors-*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                    timestamp = datetime.fromisoformat(str(row["timestamp"]))
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
                if since <= timestamp <= end:
                    yield row
