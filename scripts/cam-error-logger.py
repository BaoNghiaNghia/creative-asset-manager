#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.error_archive import (  # noqa: E402
    ERROR_RETENTION_DAYS,
    append_archived_errors,
    classify_error_message,
    iter_archived_errors,
    normalize_journal_error,
    purge_expired_archives,
)

DEFAULT_STATE_DIR = Path("/var/lib/creative-asset-manager/error-logger")


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive and inspect Creative Asset Manager system errors."
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Writable directory for archive files and journal cursor.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="Archive new CAM errors from journald.")
    collect.add_argument("--backfill-days", type=int, default=ERROR_RETENTION_DAYS)

    show = sub.add_parser("show", help="Show archived CAM errors.")
    show.add_argument("--days", type=int, default=ERROR_RETENTION_DAYS)
    show.add_argument("--limit", type=int, default=100)
    show.add_argument("--service", default="")
    show.add_argument("--search", default="")
    show.add_argument("--json", action="store_true")

    stats = sub.add_parser("stats", help="Summarize archived CAM errors.")
    stats.add_argument("--days", type=int, default=ERROR_RETENTION_DAYS)
    return parser.parse_args(argv)


def _journal_command(cursor: str | None, backfill_days: int) -> list[str]:
    command = [
        "journalctl",
        "--no-pager",
        "--output=json",
        "--unit=creative-asset-manager-*",
    ]
    if cursor:
        command.append(f"--after-cursor={cursor}")
    else:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, backfill_days))
        command.append(f"--since=@{int(since.timestamp())}")
    return command


def _load_cursor(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _save_cursor(path: Path, cursor: str) -> None:
    temporary = path.with_suffix(".new")
    temporary.write_text(cursor + "\n", encoding="utf-8")
    temporary.replace(path)


def collect(state_dir: Path, backfill_days: int) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = state_dir / "archive"
    cursor_path = state_dir / "journal.cursor"
    lock_path = state_dir / "collector.lock"

    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("error-logger: collector already running")
            return 0

        cursor = _load_cursor(cursor_path)
        process = subprocess.Popen(
            _journal_command(cursor, backfill_days),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        archived: list[dict] = []
        latest_cursor = cursor
        scanned = 0
        assert process.stdout is not None
        for raw in process.stdout:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            scanned += 1
            value = str(record.get("__CURSOR") or "").strip()
            if value:
                latest_cursor = value
            normalized = normalize_journal_error(record)
            if normalized is not None:
                archived.append(normalized)

        stderr = process.stderr.read() if process.stderr is not None else ""
        returncode = process.wait()
        if returncode != 0:
            message = (stderr or "journalctl failed").strip()
            print(f"error-logger: {message}", file=sys.stderr)
            return 1

        written = append_archived_errors(archive_dir, archived)
        removed = purge_expired_archives(archive_dir)
        if latest_cursor:
            _save_cursor(cursor_path, latest_cursor)

        print(
            "error-logger:"
            f" scanned={scanned}"
            f" archived={written}"
            f" expired_files_removed={removed}"
            f" retention_days={ERROR_RETENTION_DAYS}"
        )
        return 0


def _filtered_rows(
    state_dir: Path,
    *,
    days: int,
    service: str = "",
    search: str = "",
) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, ERROR_RETENTION_DAYS)))
    rows = list(iter_archived_errors(state_dir / "archive", since=since))
    # Re-apply the current classifier at read time so improvements to noise
    # filtering also clean historical views without rewriting archive files.
    reclassified: list[dict] = []
    for row in rows:
        captured, severity = classify_error_message(str(row.get("message") or ""))
        if not captured:
            continue
        if row.get("severity") != severity:
            row = {**row, "severity": severity}
        reclassified.append(row)
    rows = reclassified
    if service:
        needle = service.casefold()
        rows = [row for row in rows if needle in str(row.get("service") or "").casefold()]
    if search:
        needle = search.casefold()
        rows = [row for row in rows if needle in str(row.get("message") or "").casefold()]
    # Rare collector interruption can replay a cursor. De-duplicate at read time.
    unique: dict[str, dict] = {}
    no_cursor: list[dict] = []
    for row in rows:
        cursor = str(row.get("journal_cursor") or "")
        if cursor:
            unique[cursor] = row
        else:
            no_cursor.append(row)
    rows = list(unique.values()) + no_cursor
    rows.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)
    return rows


def show(state_dir: Path, days: int, limit: int, service: str, search: str, as_json: bool) -> int:
    rows = _filtered_rows(state_dir, days=days, service=service, search=search)
    rows = rows[: max(1, min(limit, 5000))]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("No archived errors found.")
        return 0
    for row in rows:
        message = str(row.get("message") or "").replace("\n", " ↩ ")
        if len(message) > 500:
            message = message[:500] + "…"
        print(
            f'{row.get("timestamp")} '
            f'[{str(row.get("severity") or "error").upper()}] '
            f'{row.get("service")} — {message}'
        )
    print(f"\nShowing {len(rows)} error(s). Retention: {ERROR_RETENTION_DAYS} days.")
    return 0


def _error_signature(row: dict) -> str:
    message = str(row.get("message") or "").strip()
    try:
        structured = json.loads(message)
    except (TypeError, ValueError, json.JSONDecodeError):
        structured = None

    if isinstance(structured, dict):
        event = str(structured.get("event") or "").strip()
        error_code = str(structured.get("error_code") or "").strip()
        outcome = str(
            structured.get("final_outcome")
            or structured.get("outcome")
            or structured.get("status")
            or ""
        ).strip()
        if error_code and error_code.lower() not in {"none", "null", "ok", "success", "0"}:
            return f"{error_code}" + (f" [{outcome}]" if outcome else "")
        if event:
            if event.startswith("{"):
                try:
                    nested = json.loads(event)
                except (TypeError, ValueError, json.JSONDecodeError):
                    nested = None
                if isinstance(nested, dict):
                    nested_code = str(nested.get("error_code") or "").strip()
                    nested_event = str(nested.get("event") or "").strip()
                    if nested_code and nested_code.lower() not in {"none", "null", "ok", "success", "0"}:
                        return nested_code
                    if nested_event:
                        return nested_event[:120]
            return event[:120]

    exception = re.search(
        r"\b([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Timeout))(?::|\b)",
        message,
    )
    if exception:
        return exception.group(1)
    if "oom-kill" in message.lower() or "oom killer" in message.lower():
        return "systemd_oom_kill"
    first_line = message.splitlines()[0] if message else "unknown"
    first_line = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f-]{27,36}\b",
        "<id>",
        first_line,
        flags=re.IGNORECASE,
    )
    return first_line[:140]


def stats(state_dir: Path, days: int) -> int:
    rows = _filtered_rows(state_dir, days=days)
    by_service = Counter(str(row.get("service") or "unknown") for row in rows)
    by_severity = Counter(str(row.get("severity") or "error") for row in rows)
    by_signature = Counter(_error_signature(row) for row in rows)
    print(f"Operational issues in last {min(max(days, 1), ERROR_RETENTION_DAYS)} day(s): {len(rows)}")
    print("Severity:", ", ".join(f"{key}={value}" for key, value in sorted(by_severity.items())) or "none")
    print("\nBy service:")
    for service, count in by_service.most_common():
        print(f"{count:6d}  {service}")
    print("\nTop patterns:")
    for signature, count in by_signature.most_common(10):
        print(f"{count:6d}  {signature}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    if args.command == "collect":
        return collect(args.state_dir, args.backfill_days)
    if args.command == "show":
        return show(args.state_dir, args.days, args.limit, args.service, args.search, args.json)
    return stats(args.state_dir, args.days)


if __name__ == "__main__":
    raise SystemExit(main())
