from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit
from uuid import uuid4

import httpx


CLIENT_VERSION = "rrugc-scout-v29"
IDLE_DIAGNOSTIC_INTERVAL_SECONDS = 30
PINTEREST_LOGIN_READY_MARKER = ".rrugc-pinterest-login-ready-v1"
SCOUT_RUNTIME_ERRORS_BEFORE_RESTART = 5
SCOUT_MAX_AUTOMATIC_RESTARTS = 3
SCOUT_FATAL_EXIT_CODE = 70
BROWSER_SESSION_MAX_AGE_SECONDS = 2 * 60 * 60
BROWSER_RUNTIME_FAILURE_RECYCLE_THRESHOLD = 2
PIN_DETAIL_CONCURRENCY = 1
PIN_RELATED_SCAN_LIMIT = 60
PIN_RELATED_MAX_SCROLL_STEPS = 12
PIN_DETAIL_TIMEOUT_MS = 15_000
PIN_DETAIL_SETTLE_MS = 650
PIN_DETAIL_NAVIGATION_PAUSE_MS = 2_500
PIN_ACCESS_GATE_COOLDOWN_SECONDS = 300
PIN_RATE_LIMIT_COOLDOWN_SECONDS = 300
PINIMG_RENDITION_SEGMENT = re.compile(r"^(?:originals|[0-9]+x(?:[0-9]+)?(?:_[A-Za-z0-9]+)?)$", re.IGNORECASE)
INITIAL_RESULTS_TIMEOUT_MS = 6_000
SCROLL_RESULTS_TIMEOUT_MS = 3_500
MANUAL_GATE_POLL_MS = 1_500
QUALITY_FIRST_RUN_CANDIDATE_CAP = 12
MAX_KEYWORDS_PER_RUN = 4
QUALITY_QUERY_SUFFIX = "authentic smartphone candid photo real people"
HUMAN_QUERY_MARKERS = (
    "selfie",
    "person",
    "people",
    " man ",
    " woman ",
    "couple",
    "family",
    "friend",
    "owner",
    "dad",
    "mom",
    "grandpa",
    "grandma",
    "wearing",
    "portrait",
)
HAND_HELD_QUERY_MARKERS = (
    "hand holding",
    "held in hand",
    "holding cap",
    "holding hat",
)
HEARTBEAT_INTERVAL_SECONDS = 10
SCOUT_HISTORY_FILENAME = "cam-pinterest-scout-history.json"
SCOUT_INSTANCE_LOCK_FILENAME = "cam-pinterest-scout-instance.json"
SCOUT_HISTORY_MAX_PINS_PER_CAMPAIGN = 50_000
HISTORY_REPLAY_EXTRA_SCROLL_BATCHES = 12
SCOUT_DEBUG_LOG_FILENAME = "pinterest-scout.jsonl"
SCOUT_DEBUG_LOG_RETENTION_DAYS = 10

_SCOUT_DEBUG_LOGGER = logging.getLogger("rrugc_scout.debug")
_SCOUT_DEBUG_LOGGER.setLevel(logging.INFO)
_SCOUT_DEBUG_LOGGER.propagate = False
_SCOUT_DEBUG_LOG_PATH: Path | None = None
_SCOUT_DEBUG_BASE_FIELDS: dict[str, Any] = {}
_SCOUT_REMOTE_LOG_QUEUE: queue.Queue[dict[str, Any]] | None = None
_SCOUT_REMOTE_LOG_THREAD: threading.Thread | None = None
_SCOUT_REMOTE_LOG_STOP = threading.Event()
_SCOUT_REMOTE_LOG_ENDPOINT: str | None = None
_SCOUT_REMOTE_LOG_TOKEN: str | None = None
_SCOUT_REMOTE_LOG_BATCH_SIZE = 50
_SCOUT_REMOTE_LOG_QUEUE_LIMIT = 5_000


def _scout_remote_log_level(event: str) -> str:
    lowered = str(event).casefold()
    if any(marker in lowered for marker in ("failed", "error", "recovering")):
        return "error"
    if any(marker in lowered for marker in ("rate_limited", "challenge", "warning")):
        return "warning"
    return "info"


def _scout_remote_log_worker() -> None:
    pending: list[dict[str, Any]] = []
    with httpx.Client(timeout=httpx.Timeout(8.0, connect=4.0)) as client:
        while not _SCOUT_REMOTE_LOG_STOP.is_set() or pending:
            current_queue = _SCOUT_REMOTE_LOG_QUEUE
            endpoint = _SCOUT_REMOTE_LOG_ENDPOINT
            token = _SCOUT_REMOTE_LOG_TOKEN
            if current_queue is None or not endpoint or not token:
                return

            if not pending:
                try:
                    pending.append(current_queue.get(timeout=0.5))
                except queue.Empty:
                    continue
                while len(pending) < _SCOUT_REMOTE_LOG_BATCH_SIZE:
                    try:
                        pending.append(current_queue.get_nowait())
                    except queue.Empty:
                        break

            try:
                response = client.post(
                    endpoint,
                    headers={"Authorization": "Bearer " + token},
                    json={"events": list(pending)},
                )
                response.raise_for_status()
                pending.clear()
            except Exception:
                if _SCOUT_REMOTE_LOG_STOP.wait(2.0):
                    return


def configure_scout_remote_log(
    *,
    base_url: str,
    agent_id: str,
    token: str,
) -> None:
    global _SCOUT_REMOTE_LOG_QUEUE
    global _SCOUT_REMOTE_LOG_THREAD
    global _SCOUT_REMOTE_LOG_ENDPOINT
    global _SCOUT_REMOTE_LOG_TOKEN

    shutdown_scout_remote_log(timeout_seconds=1.0)
    _SCOUT_REMOTE_LOG_STOP.clear()
    _SCOUT_REMOTE_LOG_QUEUE = queue.Queue(maxsize=_SCOUT_REMOTE_LOG_QUEUE_LIMIT)
    _SCOUT_REMOTE_LOG_ENDPOINT = (
        base_url.rstrip("/")
        + "/api/v1/realistic-review-ugc/scout-agents/"
        + agent_id
        + "/logs"
    )
    _SCOUT_REMOTE_LOG_TOKEN = token
    _SCOUT_REMOTE_LOG_THREAD = threading.Thread(
        target=_scout_remote_log_worker,
        name="rrugc-scout-remote-log",
        daemon=True,
    )
    _SCOUT_REMOTE_LOG_THREAD.start()


def shutdown_scout_remote_log(*, timeout_seconds: float = 3.0) -> None:
    global _SCOUT_REMOTE_LOG_QUEUE
    global _SCOUT_REMOTE_LOG_THREAD
    global _SCOUT_REMOTE_LOG_ENDPOINT
    global _SCOUT_REMOTE_LOG_TOKEN

    thread = _SCOUT_REMOTE_LOG_THREAD
    _SCOUT_REMOTE_LOG_STOP.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=max(0.0, timeout_seconds))
    _SCOUT_REMOTE_LOG_QUEUE = None
    _SCOUT_REMOTE_LOG_THREAD = None
    _SCOUT_REMOTE_LOG_ENDPOINT = None
    _SCOUT_REMOTE_LOG_TOKEN = None


def configure_scout_debug_log(
    profile_dir: Path,
    *,
    filename: str = SCOUT_DEBUG_LOG_FILENAME,
    scout_type: str = "review",
) -> Path:
    global _SCOUT_DEBUG_LOG_PATH, _SCOUT_DEBUG_BASE_FIELDS
    safe_filename = Path(str(filename or "")).name
    if not safe_filename or not safe_filename.endswith(".jsonl"):
        raise ValueError("Scout debug log filename must be a .jsonl basename.")
    log_dir = profile_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / safe_filename
    for handler in list(_SCOUT_DEBUG_LOGGER.handlers):
        _SCOUT_DEBUG_LOGGER.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=SCOUT_DEBUG_LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _SCOUT_DEBUG_LOGGER.addHandler(handler)
    _SCOUT_DEBUG_LOG_PATH = log_path
    _SCOUT_DEBUG_BASE_FIELDS = {
        "scout_type": str(scout_type or "unknown").strip() or "unknown",
    }
    scout_debug_event(
        "logging_started",
        log_path=str(log_path),
        retention_days=SCOUT_DEBUG_LOG_RETENTION_DAYS,
    )
    return log_path


def scout_debug_event(event: str, **fields: Any) -> None:
    if not _SCOUT_DEBUG_LOGGER.handlers and _SCOUT_REMOTE_LOG_QUEUE is None:
        return
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": str(event),
        "client_version": CLIENT_VERSION,
        **_SCOUT_DEBUG_BASE_FIELDS,
        **fields,
    }
    if _SCOUT_DEBUG_LOGGER.handlers:
        try:
            _SCOUT_DEBUG_LOGGER.info(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            )
        except Exception:
            # Debug logging must never interrupt Pinterest scouting.
            pass

    remote_queue = _SCOUT_REMOTE_LOG_QUEUE
    if remote_queue is not None:
        try:
            remote_queue.put_nowait(
                {
                    "event_id": uuid4().hex,
                    "event_type": str(event),
                    "level": _scout_remote_log_level(str(event)),
                    "occurred_at": payload["ts"],
                    "payload": payload,
                }
            )
        except queue.Full:
            # Remote logging is best-effort; local JSONL remains the fallback.
            pass


@dataclass(frozen=True, slots=True)
class ScoutPace:
    name: str
    initial_dwell_ms: tuple[int, int]
    inspect_dwell_ms: tuple[int, int]
    submit_batch_size: int
    submit_pause_ms: tuple[int, int]
    scroll_step_px: tuple[int, int]
    scroll_steps_per_batch: tuple[int, int]
    scroll_step_pause_ms: tuple[int, int]
    keyword_pause_ms: tuple[int, int]


SCOUT_PACES = {
    "balanced": ScoutPace(
        name="balanced",
        initial_dwell_ms=(2_500, 4_000),
        inspect_dwell_ms=(900, 1_600),
        submit_batch_size=4,
        submit_pause_ms=(900, 1_600),
        scroll_step_px=(650, 950),
        scroll_steps_per_batch=(2, 3),
        scroll_step_pause_ms=(450, 850),
        keyword_pause_ms=(1_800, 3_000),
    ),
    "careful": ScoutPace(
        name="careful",
        initial_dwell_ms=(3_800, 5_800),
        inspect_dwell_ms=(1_200, 2_200),
        submit_batch_size=3,
        submit_pause_ms=(1_250, 2_300),
        scroll_step_px=(420, 700),
        scroll_steps_per_batch=(3, 5),
        scroll_step_pause_ms=(550, 950),
        keyword_pause_ms=(3_000, 5_000),
    ),
}

SYNTHETIC_METADATA_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bai[- ]generated\b",
        r"\bartificial intelligence\b",
        r"\bmidjourney\b",
        r"\bstable diffusion\b",
        r"\bdall[- ]?e\b",
        r"\bflux ai\b",
        r"\bleonardo ai\b",
        r"\bideogram\b",
        r"\bdigital (?:art|illustration)\b",
        r"\b3d (?:render|rendering|art)\b",
        r"\bcgi\b",
        r"\bconcept art\b",
        r"\bvector (?:art|illustration)\b",
        r"\banime\b",
        r"\bcartoon\b",
        r"\bgenerative art\b",
        r"\bai art\b",
        r"\bprompt\b",
    )
)


def resolve_chrome_executable(explicit: str = "") -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise SystemExit("Chrome executable was not found: " + explicit)

    candidates: list[Path] = []
    if sys.platform == "win32":
        for root in filter(None, [
            os.getenv("PROGRAMFILES"),
            os.getenv("PROGRAMFILES(X86)"),
            os.getenv("LOCALAPPDATA"),
        ]):
            candidates.append(Path(root) / "Google/Chrome/Application/chrome.exe")
    elif sys.platform == "darwin":
        candidates.extend([
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ])
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return ""


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parse_windows_profile_owners(raw: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    chrome_pids: list[int] = []
    scout_pids: list[int] = []
    for line in str(raw or "").splitlines():
        kind, separator, pid_text = line.strip().partition("|")
        if not separator:
            continue
        try:
            pid = int(pid_text.strip())
        except ValueError:
            continue
        if pid <= 0:
            continue
        if kind.strip().casefold() == "chrome":
            chrome_pids.append(pid)
        elif kind.strip().casefold() == "scout":
            scout_pids.append(pid)
    return tuple(sorted(set(chrome_pids))), tuple(sorted(set(scout_pids)))


def _windows_profile_owners(
    profile_dir: str | Path,
) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    if sys.platform != "win32":
        return (), ()

    profile = str(Path(profile_dir).expanduser().resolve())
    profile_literal = _powershell_literal(profile)
    scout_marker = _powershell_literal(r"rrugc_scout\scout.py")
    script = (
        "$needle = (" + profile_literal + ").ToLowerInvariant(); "
        "$plainProfileArg = '--user-data-dir=' + $needle; "
        "$quotedProfileArg = '--user-data-dir="' + $needle + '"'; "
        "$scoutMarker = (" + scout_marker + ").ToLowerInvariant(); "
        "$currentPid = " + str(os.getpid()) + "; "
        "Get-CimInstance Win32_Process | ForEach-Object { "
        "$name = [string]$_.Name; "
        "$cmd = [string]$_.CommandLine; "
        "$pidValue = [int]$_.ProcessId; "
        "if ($pidValue -ne $currentPid -and -not [string]::IsNullOrWhiteSpace($cmd)) { "
        "$lower = $cmd.ToLowerInvariant(); "
        "if ($name -ieq 'chrome.exe' "
        "-and ($lower.Contains($plainProfileArg) -or $lower.Contains($quotedProfileArg)) "
        "-and -not $lower.Contains('--type=')) { "
        "Write-Output ('chrome|' + $pidValue) "
        "} elseif (($name -ieq 'python.exe' -or $name -ieq 'pythonw.exe') "
        "-and $lower.Contains($scoutMarker) -and $lower.Contains($needle)) { "
        "Write-Output ('scout|' + $pidValue) "
        "} "
        "} "
        "}"
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _parse_windows_profile_owners(result.stdout)


def _terminate_windows_process_trees(pids: tuple[int, ...]) -> int:
    if sys.platform != "win32":
        return 0
    terminated = 0
    for pid in pids:
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            terminated += 1
    return terminated


def _terminate_windows_profile_chrome(pids: tuple[int, ...]) -> int:
    return _terminate_windows_process_trees(pids)


def _clear_stale_profile_runtime_files(profile_dir: str | Path) -> tuple[str, ...]:
    profile = Path(profile_dir).expanduser().resolve()
    removed: list[str] = []
    for name in (
        "SingletonCookie",
        "SingletonLock",
        "SingletonSocket",
        "lockfile",
        "DevToolsActivePort",
    ):
        path = profile / name
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
                removed.append(name)
        except OSError:
            continue
    return tuple(removed)


def _looks_like_profile_launch_collision(error: BaseException) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "target page, context or browser has been closed",
            "profile is already in use",
            "exitcode=21",
            "processsingleton",
        )
    )


def _looks_like_browser_runtime_failure(error: BaseException) -> bool:
    message = (error.__class__.__name__ + " " + str(error)).casefold()
    return any(
        marker in message
        for marker in (
            "targetclosederror",
            "target page, context or browser has been closed",
            "browser has been closed",
            "browser context closed",
            "page crashed",
            "page has been closed",
            "connection closed",
            "connection terminated",
        )
    )


def _browser_session_needs_recycle(
    page: Any,
    detail_page: Any,
    started_at: float,
    *,
    now: float | None = None,
) -> bool:
    if page is None or detail_page is None:
        return True
    try:
        if page.is_closed() or detail_page.is_closed():
            return True
    except Exception:
        return True
    current = time.monotonic() if now is None else now
    return current - started_at >= BROWSER_SESSION_MAX_AGE_SECONDS


class ScoutProfileLock:
    """Atomic local ownership for the dedicated persistent Scout profile."""

    def __init__(self, profile_dir: str | Path) -> None:
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.path = self.profile_dir / SCOUT_INSTANCE_LOCK_FILENAME
        self.acquired = False

    def _owner_pid(self) -> int | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return pid if pid > 0 else None

    def _owner_is_live_scout(self, pid: int | None) -> bool:
        if pid is None:
            return False
        if pid == os.getpid():
            return True
        if sys.platform != "win32":
            return False
        owners = _windows_profile_owners(self.profile_dir)
        return owners is not None and pid in owners[1]

    def acquire(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "version": CLIENT_VERSION,
                "profile": str(self.profile_dir),
                "started_at": time.time(),
            },
            separators=(",", ":"),
        ).encode("utf-8")

        for _attempt in range(2):
            try:
                fd = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                owner_pid = self._owner_pid()
                if self._owner_is_live_scout(owner_pid):
                    raise RuntimeError(
                        "Another Pinterest Auto Scout already owns this profile lock "
                        "(PID " + str(owner_pid) + "). Use the existing Scout instance "
                        "or close it before starting another one."
                    )
                try:
                    self.path.unlink()
                    print("Removed stale Pinterest Scout instance lock.")
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise RuntimeError(
                        "Could not replace stale Pinterest Scout instance lock: "
                        + str(self.path)
                    ) from exc
                continue

            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            self.acquired = True
            return

        raise RuntimeError("Could not acquire the Pinterest Scout profile lock.")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            if self._owner_pid() == os.getpid():
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False



def _recover_windows_scout_profile(profile_dir: str | Path) -> None:
    owners = _windows_profile_owners(profile_dir)
    if owners is None:
        print(
            "Could not inspect Windows Chrome profile ownership; "
            "retrying launch without process cleanup."
        )
        return

    chrome_pids, scout_pids = owners

    if scout_pids:
        print(
            "Legacy Scout process(es) detected for this profile and left untouched: "
            + ", ".join(str(pid) for pid in scout_pids)
        )

    if chrome_pids:
        print(
            "Closing only root Chrome process tree(s) using the dedicated Pinterest "
            "Scout profile: "
            + ", ".join(str(pid) for pid in chrome_pids)
        )
        _terminate_windows_profile_chrome(chrome_pids)
        time.sleep(0.75)
        remaining = _windows_profile_owners(profile_dir)
        if remaining is not None and remaining[0]:
            raise RuntimeError(
                "The dedicated Pinterest Scout Chrome profile is still in use by root "
                "Chrome PID(s) "
                + ", ".join(str(pid) for pid in remaining[0])
                + ". Other Chrome profiles were not touched. Close only the Pinterest "
                "Scout profile window and run START_SCOUT.bat again."
            )

    removed = _clear_stale_profile_runtime_files(profile_dir)
    if removed:
        print("Cleared stale Scout profile runtime files: " + ", ".join(removed))



def pinterest_login_marker_path(profile_dir: str | Path) -> Path:
    return Path(profile_dir).expanduser().resolve() / PINTEREST_LOGIN_READY_MARKER


def pinterest_login_ready(profile_dir: str | Path) -> bool:
    return pinterest_login_marker_path(profile_dir).is_file()


def mark_pinterest_login_ready(profile_dir: str | Path) -> None:
    marker = pinterest_login_marker_path(profile_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(CLIENT_VERSION + "\n", encoding="utf-8")


def clear_pinterest_login_ready(profile_dir: str | Path) -> None:
    try:
        pinterest_login_marker_path(profile_dir).unlink()
    except FileNotFoundError:
        pass


def bootstrap_login(profile_dir: str, chrome_executable: str = "") -> None:
    chrome = resolve_chrome_executable(chrome_executable)
    if not chrome:
        raise SystemExit(
            "Google Chrome was not found. Install Chrome or pass --chrome-executable."
        )
    profile = str(Path(profile_dir).expanduser().resolve())
    Path(profile).mkdir(parents=True, exist_ok=True)
    print("Opening a normal Chrome window for manual Pinterest sign-in.")
    print("Profile: " + profile)
    print(
        "Sign in to Pinterest in this normal Chrome window before Scout starts. "
        "For Scout profiles, use Pinterest email/password instead of Continue with Google: "
        "Google may reject OAuth in dedicated/automation-associated browser profiles with "
        "'This browser or app may not be secure'. If needed, use Pinterest's password-reset "
        "flow to create/reset a Pinterest password for the account email."
    )
    print(
        "When Pinterest is fully signed in and the home/feed opens normally, close this "
        "Chrome window. Scout will verify the saved session before doing any search work."
    )
    process = subprocess.Popen([
        chrome,
        "--user-data-dir=" + profile,
        "--new-window",
        "--no-first-run",
        "https://www.pinterest.com/login/",
    ])
    process.wait()
    print("Bootstrap Chrome closed. Scout will now verify the Pinterest session.")


class PinterestAccessGateError(RuntimeError):
    def __init__(self, gate: str) -> None:
        self.gate = gate
        super().__init__("Pinterest access gate: " + gate)


class PinterestRateLimitedError(RuntimeError):
    pass


class PinterestVideoPinError(RuntimeError):
    pass


class ScoutRestartRequested(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        healthy_progress: bool,
        last_error_type: str,
    ) -> None:
        self.error_code = error_code
        self.healthy_progress = healthy_progress
        self.last_error_type = last_error_type
        super().__init__(error_code)


class ScoutFatalStop(RuntimeError):
    pass


def scout_restart_delay_seconds(attempt: int) -> int:
    return min(30, 5 * max(1, int(attempt)))


async def report_scout_fatal_status(
    *,
    base_url: str,
    agent_id: str,
    token: str,
    machine_label: str,
    error_code: str,
) -> None:
    try:
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
        ) as client:
            response = await client.post(
                f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/heartbeat",
                json={
                    "status": "error",
                    "client_version": CLIENT_VERSION,
                    "machine_label": machine_label,
                    "error_code": error_code,
                },
            )
            response.raise_for_status()
    except Exception as exc:
        scout_debug_event(
            "scout_fatal_status_report_failed",
            error_code=error_code,
            error_type=exc.__class__.__name__,
        )


def guard_pinterest_response(response: Any) -> None:
    status = int(getattr(response, "status", 0) or 0)
    if status == 429:
        raise PinterestRateLimitedError("Pinterest returned HTTP 429")
    if status in {401, 403}:
        raise PinterestAccessGateError("access_denied")


@dataclass(frozen=True, slots=True)
class Candidate:
    pin_url: str
    image_url: str
    alt_text: str | None = None
    context_text: str | None = None

    def as_json(self) -> dict[str, str | None]:
        return {
            "pin_url": self.pin_url,
            "image_url": self.image_url,
            "alt_text": self.alt_text,
        }


def pin_history_key(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme == "https"
        and (host == "pinterest.com" or host.endswith(".pinterest.com"))
        and parsed.path.startswith("/pin/")
    ):
        return "https://www.pinterest.com" + parsed.path.rstrip("/") + "/"
    return str(value or "").strip()


class ScoutHistory:
    """Small durable Pin history stored beside the persistent Chrome profile."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_pins_per_campaign: int = SCOUT_HISTORY_MAX_PINS_PER_CAMPAIGN,
    ) -> None:
        self.path = Path(path)
        self.max_pins_per_campaign = max(100, int(max_pins_per_campaign))
        self.data: dict[str, Any] = {"version": 2, "campaigns": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("campaigns"), dict):
                self.data = payload
        except (FileNotFoundError, OSError, ValueError, TypeError):
            pass

    def seen_pin_keys(self, campaign_id: str) -> set[str]:
        campaigns = self.data.get("campaigns")
        if not isinstance(campaigns, dict):
            return set()
        row = campaigns.get(str(campaign_id))
        if not isinstance(row, dict):
            return set()
        pins = row.get("seen_pins")
        if not isinstance(pins, list):
            return set()
        return {
            pin_history_key(value)
            for value in pins
            if isinstance(value, str) and value.strip()
        }

    def seen_asset_keys(self, campaign_id: str) -> set[str]:
        campaigns = self.data.get("campaigns")
        if not isinstance(campaigns, dict):
            return set()
        row = campaigns.get(str(campaign_id))
        if not isinstance(row, dict):
            return set()
        assets = row.get("seen_assets")
        if not isinstance(assets, list):
            return set()
        return {
            str(value).strip()
            for value in assets
            if isinstance(value, str) and value.strip()
        }

    def expanded_seed_keys(self, campaign_id: str) -> set[str]:
        campaigns = self.data.get("campaigns")
        if not isinstance(campaigns, dict):
            return set()
        row = campaigns.get(str(campaign_id))
        if not isinstance(row, dict):
            return set()
        seeds = row.get("expanded_related_seeds")
        if not isinstance(seeds, list):
            return set()
        return {
            pin_history_key(value)
            for value in seeds
            if isinstance(value, str) and value.strip()
        }

    def remember_expanded_seed(self, campaign_id: str, pin_url: str) -> bool:
        campaigns = self.data.setdefault("campaigns", {})
        if not isinstance(campaigns, dict):
            campaigns = {}
            self.data["campaigns"] = campaigns
        key = str(campaign_id)
        campaign = campaigns.setdefault(
            key,
            {"seen_pins": [], "expanded_related_seeds": []},
        )
        if not isinstance(campaign, dict):
            campaign = {"seen_pins": [], "expanded_related_seeds": []}
            campaigns[key] = campaign
        seeds = campaign.get("expanded_related_seeds")
        if not isinstance(seeds, list):
            seeds = []
        normalized = pin_history_key(pin_url)
        known = {
            pin_history_key(value)
            for value in seeds
            if isinstance(value, str) and value.strip()
        }
        if not normalized or normalized in known:
            return False
        seeds.append(normalized)
        campaign["expanded_related_seeds"] = seeds[-2000:]
        self.save()
        return True

    def remember(self, campaign_id: str, rows: list[Candidate]) -> int:
        if not rows:
            return 0
        campaigns = self.data.setdefault("campaigns", {})
        if not isinstance(campaigns, dict):
            campaigns = {}
            self.data["campaigns"] = campaigns
        key = str(campaign_id)
        campaign = campaigns.setdefault(
            key,
            {"seen_pins": [], "seen_assets": []},
        )
        if not isinstance(campaign, dict):
            campaign = {"seen_pins": [], "seen_assets": []}
            campaigns[key] = campaign
        pins = campaign.get("seen_pins")
        if not isinstance(pins, list):
            pins = []
        assets = campaign.get("seen_assets")
        if not isinstance(assets, list):
            assets = []
        ordered_pins = [
            pin_history_key(value)
            for value in pins
            if isinstance(value, str) and value.strip()
        ]
        ordered_assets = [
            str(value).strip()
            for value in assets
            if isinstance(value, str) and value.strip()
        ]
        known_pins = set(ordered_pins)
        known_assets = set(ordered_assets)
        added = 0
        changed = False
        for row in rows:
            pin = pin_history_key(row.pin_url)
            asset = pinimg_asset_key(row.image_url)
            if pin and pin not in known_pins:
                known_pins.add(pin)
                ordered_pins.append(pin)
                added += 1
                changed = True
            if asset and asset not in known_assets:
                known_assets.add(asset)
                ordered_assets.append(asset)
                changed = True
        campaign["seen_pins"] = ordered_pins[-self.max_pins_per_campaign:]
        campaign["seen_assets"] = ordered_assets[-self.max_pins_per_campaign:]
        self.data["version"] = 2
        if changed:
            self.save()
        return added

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(
                json.dumps(self.data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            print("Scout history could not be saved: " + exc.__class__.__name__)


def allowed_pin(value: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").rstrip(".").lower()
    return (
        parsed.scheme == "https"
        and (host == "pinterest.com" or host.endswith(".pinterest.com"))
        and parsed.path.startswith("/pin/")
    )


def allowed_image(value: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").rstrip(".").lower()
    return (
        parsed.scheme == "https"
        and (host == "pinimg.com" or host.endswith(".pinimg.com"))
    )


def quality_search_query(value: str) -> str:
    clean = " ".join(str(value or "").split())
    if not clean:
        return clean
    lowered = " " + clean.casefold() + " "
    if any(marker in lowered for marker in HAND_HELD_QUERY_MARKERS):
        if "real human hand" not in lowered:
            return clean + " real human hand product review"
        return clean
    has_photo_context = any(
        marker in lowered
        for marker in ("photo", "photography", "candid", "lifestyle")
    )
    has_human_context = any(marker in lowered for marker in HUMAN_QUERY_MARKERS)
    if has_photo_context:
        if not has_human_context:
            return clean + " real person product review"
        return clean
    return clean + " " + QUALITY_QUERY_SUFFIX


def synthetic_metadata_reason(candidate: Candidate) -> str | None:
    text = " ".join(
        part for part in (candidate.alt_text, candidate.context_text) if part
    )
    if not text:
        return None
    for pattern in SYNTHETIC_METADATA_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def quality_prefilter(rows: list[Candidate]) -> tuple[list[Candidate], int]:
    accepted: list[Candidate] = []
    filtered = 0
    for row in rows:
        if synthetic_metadata_reason(row) is not None:
            filtered += 1
            continue
        accepted.append(row)
    return accepted, filtered


def normalize_candidates(rows: list[dict[str, Any]]) -> list[Candidate]:
    by_pin: dict[str, Candidate] = {}
    order: list[str] = []
    for row in rows:
        pin_url = str(row.get("pin_url") or "").strip()
        image_url = str(row.get("image_url") or "").strip()
        alt_text = str(row.get("alt_text") or "").strip() or None
        context_text = str(row.get("context_text") or "").strip()[:1200] or None
        if row.get("is_video") is True:
            continue
        if not allowed_pin(pin_url) or not allowed_image(image_url):
            continue
        candidate = Candidate(pin_url, image_url, alt_text, context_text)
        existing = by_pin.get(pin_url)
        if existing is None:
            by_pin[pin_url] = candidate
            order.append(pin_url)
            continue
        if pinimg_rendition_score(candidate.image_url) > pinimg_rendition_score(
            existing.image_url
        ):
            by_pin[pin_url] = replace(
                candidate,
                alt_text=candidate.alt_text or existing.alt_text,
                context_text=candidate.context_text or existing.context_text,
            )
    return [by_pin[pin_url] for pin_url in order]


async def extract_pin_links(page: Any) -> list[str]:
    """Return every Pinterest Pin href currently present in the DOM.

    Pinterest frequently virtualizes result cards so the clickable /pin/
    anchor remains while its image is moved behind an overlay or temporarily
    has no pinimg src. Detail-first Scout flows must still open that Pin.
    """
    values = await page.evaluate(
        r"""() => Array.from(document.querySelectorAll('a[href*="/pin/"]'))
          .map((anchor) => anchor.href || anchor.getAttribute('href') || '')
          .filter(Boolean)"""
    )
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        raw = str(value or "").strip()
        key = pin_history_key(raw)
        if not key or key in seen or not allowed_pin(key):
            continue
        seen.add(key)
        result.append(key)
    return result


def merge_pin_link_candidates(
    visible: list[Candidate],
    pin_urls: list[str],
) -> list[Candidate]:
    """Keep rich candidates and add Pin-only placeholders for detail resolve."""
    result = list(visible)
    seen = {
        pin_history_key(candidate.pin_url)
        for candidate in visible
        if pin_history_key(candidate.pin_url)
    }
    for pin_url in pin_urls:
        key = pin_history_key(pin_url)
        if not key or key in seen:
            continue
        seen.add(key)
        # Empty image_url is intentional: resolve_pin_details() opens the Pin
        # detail page and replaces it with the real close-up/meta image.
        result.append(Candidate(key, ""))
    return result


async def extract_visible(page: Any) -> list[Candidate]:
    rows = await page.evaluate(
        r"""() => {
          const out = [];
          const seen = new Set();
          const pinImageSelector =
            'img[src*="pinimg.com"], img[srcset*="pinimg.com"]';

          const bestSrc = (image) => {
            const candidates = [];
            const srcset = image.getAttribute('srcset') || '';
            for (const part of srcset.split(',')) {
              const match = part.trim().match(/^(\S+)\s+(\d+)w$/);
              if (match) candidates.push([Number(match[2]), match[1]]);
            }
            candidates.sort((a, b) => b[0] - a[0]);
            return (candidates[0] && candidates[0][1])
              || image.currentSrc
              || image.src
              || '';
          };

          const renderedArea = (node) => {
            const rect = node?.getBoundingClientRect?.();
            if (!rect) return 0;
            return Math.max(0, rect.width) * Math.max(0, rect.height);
          };

          const pinImages = Array.from(
            document.querySelectorAll(pinImageSelector)
          );

          const chooseLargest = (images) => {
            const rows = Array.from(images || []).filter(Boolean);
            rows.sort((a, b) => {
              const areaDelta = renderedArea(b) - renderedArea(a);
              if (areaDelta) return areaDelta;
              return (b.naturalWidth || 0) - (a.naturalWidth || 0);
            });
            return rows[0] || null;
          };

          const localImageForAnchor = (anchor) => {
            let node = anchor;
            for (let depth = 0; node && depth < 10; depth += 1) {
              const images = Array.from(node.querySelectorAll?.(
                pinImageSelector
              ) || []);
              const pinLinks = Array.from(node.querySelectorAll?.(
                'a[href*="/pin/"]'
              ) || []);
              if (
                images.length
                && pinLinks.length >= 1
                && pinLinks.length <= 4
              ) {
                return {
                  image: chooseLargest(images),
                  card: node,
                  strategy: 'ancestor',
                };
              }
              node = node.parentElement;
            }
            return null;
          };

          const rectForAnchor = (anchor) => {
            let node = anchor;
            for (let depth = 0; node && depth < 6; depth += 1) {
              const rect = node.getBoundingClientRect?.();
              if (rect && rect.width > 8 && rect.height > 8) {
                return rect;
              }
              node = node.parentElement;
            }
            return anchor.getBoundingClientRect?.() || null;
          };

          const nearestImageForAnchor = (anchor) => {
            const anchorRect = rectForAnchor(anchor);
            if (!anchorRect) return null;
            const ax = anchorRect.left + anchorRect.width / 2;
            const ay = anchorRect.top + anchorRect.height / 2;
            let best = null;
            let bestScore = Number.POSITIVE_INFINITY;
            for (const image of pinImages) {
              const rect = image.getBoundingClientRect?.();
              if (!rect || rect.width < 20 || rect.height < 20) continue;
              const ix = rect.left + rect.width / 2;
              const iy = rect.top + rect.height / 2;
              const dx = ix - ax;
              const dy = iy - ay;
              const distance = Math.sqrt(dx * dx + dy * dy);
              const inside = (
                ix >= anchorRect.left - 24
                && ix <= anchorRect.right + 24
                && iy >= anchorRect.top - 24
                && iy <= anchorRect.bottom + 24
              );
              const score = distance - (inside ? 10000 : 0)
                - Math.min(renderedArea(image), 200000) / 10000;
              if (score < bestScore) {
                bestScore = score;
                best = image;
              }
            }
            if (!best) return null;
            const bestRect = best.getBoundingClientRect?.();
            const bx = bestRect.left + bestRect.width / 2;
            const by = bestRect.top + bestRect.height / 2;
            const distance = Math.sqrt((bx - ax) ** 2 + (by - ay) ** 2);
            const maxDistance = Math.max(
              360,
              Math.max(anchorRect.width, anchorRect.height) * 2.5
            );
            return distance <= maxDistance ? best : null;
          };

          const collect = (anchor, image, card, strategy) => {
            if (!anchor || !image) return;
            const href = anchor.href || anchor.getAttribute('href') || '';
            if (!href.includes('/pin/')) return;

            const mediaRoot = card || anchor;
            const isVideo = Boolean(mediaRoot?.querySelector?.([
              'video',
              '[data-test-id*="video" i]',
              '[data-test-id*="story-pin" i]',
              '[data-test-id*="idea-pin" i]',
              '[aria-label*="video" i]',
            ].join(',')));
            if (isVideo) return;

            const src = bestSrc(image);
            if (!src || !src.includes('pinimg.com')) return;
            const key = href + '\n' + src;
            if (seen.has(key)) return;
            seen.add(key);

            const contextText = (card?.textContent || '').trim().slice(0, 1200);
            out.push({
              pin_url: href,
              image_url: src,
              alt_text: image.alt
                || image.getAttribute('aria-label')
                || anchor.getAttribute('aria-label')
                || null,
              context_text: contextText || null,
              pairing_strategy: strategy || null,
            });
          };

          const anchors = Array.from(
            document.querySelectorAll('a[href*="/pin/"]')
          );
          const handledAnchors = new Set();

          for (const anchor of anchors) {
            const href = anchor.href || anchor.getAttribute('href') || '';
            if (!href || handledAnchors.has(href)) continue;

            const directImages = Array.from(
              anchor.querySelectorAll?.(pinImageSelector) || []
            );
            if (directImages.length) {
              collect(anchor, chooseLargest(directImages), anchor, 'direct');
              handledAnchors.add(href);
              continue;
            }

            const local = localImageForAnchor(anchor);
            if (local?.image) {
              collect(anchor, local.image, local.card, local.strategy);
              handledAnchors.add(href);
              continue;
            }

            const nearest = nearestImageForAnchor(anchor);
            if (nearest) {
              collect(anchor, nearest, anchor.parentElement, 'geometry');
              handledAnchors.add(href);
            }
          }

          // Image-first fallback for cards whose clickable Pin overlay is not
          // an ancestor of the image. Walk upward farther than the old fixed
          // parent-depth heuristic and stop before reaching a multi-card root.
          for (const image of pinImages) {
            let node = image;
            let anchor = image.closest?.('a[href*="/pin/"]') || null;
            let card = anchor || null;
            for (let depth = 0; !anchor && node && depth < 10; depth += 1) {
              const links = Array.from(node.querySelectorAll?.(
                'a[href*="/pin/"]'
              ) || []);
              if (links.length === 1) {
                anchor = links[0];
                card = node;
                break;
              }
              if (links.length > 4) break;
              node = node.parentElement;
            }
            collect(anchor, image, card, 'image-first');
          }

          return out;
        }"""
    )
    return normalize_candidates(rows)


async def extract_visible_pin_candidates(page: Any) -> list[Candidate]:
    """Discover all Pin links, even when Pinterest does not pair them with images."""
    visible = await extract_visible(page)
    pin_urls = await extract_pin_links(page)
    return merge_pin_link_candidates(visible, pin_urls)


def pinimg_asset_key(value: str) -> str:
    """Return the rendition-independent Pinterest CDN path for one image asset."""
    if not allowed_image(value):
        return ""
    parsed = urlsplit(value)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and PINIMG_RENDITION_SEGMENT.fullmatch(parts[0]):
        return "/".join(parts[1:])
    return "/".join(parts)


def pinimg_rendition_score(value: str) -> int:
    """Approximate Pinterest rendition quality without downloading the image."""
    if not allowed_image(value):
        return -1
    parts = [part for part in urlsplit(value).path.split("/") if part]
    if not parts:
        return 0
    rendition = parts[0].lower()
    if rendition == "originals":
        return 10_000_000
    match = re.match(r"^(\d+)x(?:([0-9]+))?", rendition, re.IGNORECASE)
    if not match:
        return 0
    width = int(match.group(1))
    height = int(match.group(2) or 0)
    return max(width, height)


def choose_pin_detail_candidate(
    seed: Candidate,
    rows: list[dict[str, Any]],
) -> Candidate:
    """Prefer the Pin-detail image for the discovered Pin without drifting to related Pins."""
    seed_key = pinimg_asset_key(seed.image_url)
    source_priority = {
        "meta": 5,
        "jsonld": 4,
        "closeup": 3,
        "image": 2,
        "seed": 1,
    }
    candidates: list[tuple[tuple[int, int, int, int], str, str | None]] = [
        (
            (
                int(bool(seed_key)),
                pinimg_rendition_score(seed.image_url),
                source_priority["seed"],
                0,
            ),
            seed.image_url,
            seed.alt_text,
        )
    ]
    for row in rows:
        url = str(row.get("url") or "").strip()
        if not allowed_image(url):
            continue
        source = str(row.get("source") or "image").strip().lower()
        width = int(row.get("width") or 0)
        height = int(row.get("height") or 0)
        same_asset = int(bool(seed_key) and pinimg_asset_key(url) == seed_key)
        priority = source_priority.get(source, 0)
        rendition = pinimg_rendition_score(url)
        area = max(0, width) * max(0, height)
        candidates.append(
            (
                (same_asset, rendition, priority, area),
                url,
                str(row.get("alt_text") or "").strip() or None,
            )
        )
    _, image_url, alt_text = max(candidates, key=lambda item: item[0])
    return replace(
        seed,
        image_url=image_url,
        alt_text=alt_text or seed.alt_text,
    )


async def extract_pin_detail_candidate(page: Any, seed: Candidate) -> Candidate:
    """Resolve a discovered Pin through its detail page before submission."""
    response = await page.goto(
        seed.pin_url,
        wait_until="domcontentloaded",
        timeout=PIN_DETAIL_TIMEOUT_MS,
    )
    guard_pinterest_response(response)
    await page.wait_for_timeout(PIN_DETAIL_SETTLE_MS)
    gate = await access_gate(page)
    if gate is not None:
        raise PinterestAccessGateError(gate)
    is_video_pin = await page.evaluate(
        r"""() => {
          const ogType = (
            document.querySelector('meta[property="og:type"]')?.getAttribute('content') || ''
          ).toLowerCase();
          return Boolean(
            document.querySelector('video')
            || document.querySelector('meta[property^="og:video"]')
            || document.querySelector('meta[name="twitter:player"]')
            || ogType.includes('video')
          );
        }"""
    )
    if is_video_pin:
        raise PinterestVideoPinError("Pinterest video Pin is not eligible for image Scout")
    rows = await page.evaluate(
        r"""() => {
          const out = [];
          const push = (url, source, width = 0, height = 0, altText = null) => {
            if (!url || typeof url !== 'string') return;
            out.push({
              url,
              source,
              width: Number(width || 0),
              height: Number(height || 0),
              alt_text: altText || null,
            });
          };

          const metaSelectors = [
            'meta[property="og:image"]',
            'meta[property="og:image:secure_url"]',
            'meta[name="twitter:image"]',
            'meta[name="twitter:image:src"]',
            'meta[itemprop="image"]',
          ];
          for (const selector of metaSelectors) {
            for (const node of document.querySelectorAll(selector)) {
              push(node.getAttribute('content'), 'meta');
            }
          }

          const visitJson = (value) => {
            if (!value) return;
            if (typeof value === 'string') {
              if (value.includes('pinimg.com')) push(value, 'jsonld');
              return;
            }
            if (Array.isArray(value)) {
              for (const child of value) visitJson(child);
              return;
            }
            if (typeof value !== 'object') return;
            for (const [key, child] of Object.entries(value)) {
              if (['image', 'contentUrl', 'thumbnailUrl', 'url'].includes(key)) {
                visitJson(child);
              } else if (typeof child === 'object') {
                visitJson(child);
              }
            }
          };
          for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
            try { visitJson(JSON.parse(node.textContent || 'null')); } catch (_) {}
          }

          const closeupSelectors = [
            'img[data-test-id="pin-closeup-image"]',
            '[data-test-id*="closeup" i] img',
            '[data-test-id*="pin" i] img[src*="pinimg.com"]',
          ];
          const seen = new Set();
          for (const selector of closeupSelectors) {
            for (const image of document.querySelectorAll(selector)) {
              if (seen.has(image)) continue;
              seen.add(image);
              const srcset = image.getAttribute('srcset') || '';
              for (const part of srcset.split(',')) {
                const match = part.trim().match(/^(\S+)\s+(\d+)w$/);
                if (match) {
                  push(match[1], 'closeup', Number(match[2]), 0, image.alt || null);
                }
              }
              push(
                image.currentSrc || image.src,
                'closeup',
                image.naturalWidth || image.width || 0,
                image.naturalHeight || image.height || 0,
                image.alt || null,
              );
            }
          }

          for (const image of document.querySelectorAll('img[src*="pinimg.com"]')) {
            const srcset = image.getAttribute('srcset') || '';
            for (const part of srcset.split(',')) {
              const match = part.trim().match(/^(\S+)\s+(\d+)w$/);
              if (match) {
                push(match[1], 'image', Number(match[2]), 0, image.alt || null);
              }
            }
            push(
              image.currentSrc || image.src,
              'image',
              image.naturalWidth || image.width || 0,
              image.naturalHeight || image.height || 0,
              image.alt || null,
            );
          }
          return out;
        }"""
    )
    return choose_pin_detail_candidate(seed, rows if isinstance(rows, list) else [])


async def extract_related_candidates(
    page: Any,
    seed: Candidate,
    *,
    pace: ScoutPace,
    limit: int = PIN_RELATED_SCAN_LIMIT,
) -> list[Candidate]:
    """Collect the first related Pins shown under one approved Pin detail page."""
    wanted = max(1, min(int(limit), PIN_RELATED_SCAN_LIMIT))
    response = await page.goto(
        seed.pin_url,
        wait_until="domcontentloaded",
        timeout=PIN_DETAIL_TIMEOUT_MS,
    )
    guard_pinterest_response(response)
    await page.wait_for_timeout(PIN_DETAIL_SETTLE_MS)
    gate = await access_gate(page)
    if gate is not None:
        raise PinterestAccessGateError(gate)

    seed_key = pin_history_key(seed.pin_url)
    collected: dict[str, Candidate] = {}
    for step in range(PIN_RELATED_MAX_SCROLL_STEPS + 1):
        visible = await extract_visible_pin_candidates(page)
        for row in visible:
            key = pin_history_key(row.pin_url)
            if not key or key == seed_key or key in collected:
                continue
            collected[key] = row
            if len(collected) >= wanted:
                break
        if len(collected) >= wanted or step >= PIN_RELATED_MAX_SCROLL_STEPS:
            break
        previous_count = await loaded_pin_count(page)
        await page.mouse.wheel(0, random.randint(*pace.scroll_step_px))
        await page.wait_for_timeout(random.randint(*pace.scroll_step_pause_ms))
        await wait_for_pin_growth(
            page,
            previous_count=previous_count,
            timeout_ms=3_500,
        )

    return list(collected.values())[:wanted]


async def resolve_pin_details(
    search_page: Any,
    rows: list[Candidate],
    *,
    detail_page: Any | None = None,
    concurrency: int = PIN_DETAIL_CONCURRENCY,
    fallback_on_error: bool = True,
) -> list[Candidate]:
    """Resolve Pin details sequentially through one reusable detail tab."""
    if not rows:
        return []

    if int(concurrency) != 1:
        print(
            "Detail concurrency is forced to 1 in low-footprint mode; "
            + str(concurrency)
            + " was requested."
        )

    owned_page = False
    if detail_page is None:
        context = getattr(search_page, "context", None)
        new_page = getattr(context, "new_page", None) if context is not None else None
        if not callable(new_page):
            return rows
        detail_page = await new_page()
        owned_page = True

    resolved: list[Candidate] = []
    try:
        for index, seed in enumerate(rows):
            try:
                bring_to_front = getattr(detail_page, "bring_to_front", None)
                if callable(bring_to_front):
                    await bring_to_front()
                scout_debug_event(
                    "pinterest_pin_detail_opening",
                    pin_url=seed.pin_url,
                    seed_has_image=allowed_image(seed.image_url),
                    index=index + 1,
                    total=len(rows),
                )
                candidate = await extract_pin_detail_candidate(detail_page, seed)
                resolved.append(candidate)
                scout_debug_event(
                    "pinterest_pin_detail_resolved",
                    pin_url=candidate.pin_url,
                    image_url=candidate.image_url,
                    resolved_image=allowed_image(candidate.image_url),
                    index=index + 1,
                    total=len(rows),
                )
            except (PinterestAccessGateError, PinterestRateLimitedError):
                raise
            except PinterestVideoPinError:
                scout_debug_event(
                    "pinterest_pin_detail_video_skipped",
                    pin_url=seed.pin_url,
                    index=index + 1,
                    total=len(rows),
                )
                print("Pinterest video Pin skipped: " + seed.pin_url)
                continue
            except Exception as exc:
                if _looks_like_browser_runtime_failure(exc):
                    raise
                scout_debug_event(
                    "pinterest_pin_detail_failed",
                    pin_url=seed.pin_url,
                    error_type=exc.__class__.__name__,
                    error=str(exc)[:500],
                    index=index + 1,
                    total=len(rows),
                )
                print(
                    "Pinterest Pin detail "
                    + ("fallback: " if fallback_on_error else "failed: ")
                    + seed.pin_url
                    + " ("
                    + exc.__class__.__name__
                    + ")"
                )
                if fallback_on_error:
                    resolved.append(seed)

            if index + 1 < len(rows):
                await detail_page.wait_for_timeout(PIN_DETAIL_NAVIGATION_PAUSE_MS)
    finally:
        if owned_page:
            try:
                await detail_page.close()
            except Exception:
                pass

    return resolved


async def access_gate(page: Any) -> str | None:
    current_url = str(getattr(page, "url", "") or "").lower()
    if "/login" in current_url:
        return "login"
    if "/challenge" in current_url or "captcha" in current_url:
        return "challenge"
    detected = await page.evaluate(
        r"""() => {
          const selectors = [
            'iframe[src*="captcha" i]',
            '[data-test-id*="captcha" i]',
            '[id*="captcha" i]',
          ];
          const visible = (node) => {
            const style = window.getComputedStyle(node);
            if (
              style.display === 'none'
              || style.visibility === 'hidden'
              || Number(style.opacity || '1') === 0
            ) return false;
            const rect = node.getBoundingClientRect();
            return (
              rect.width >= 20
              && rect.height >= 20
              && rect.bottom > 0
              && rect.right > 0
              && rect.top < window.innerHeight
              && rect.left < window.innerWidth
            );
          };
          return selectors.some((selector) =>
            Array.from(document.querySelectorAll(selector)).some(visible)
          );
        }"""
    )
    if detected:
        return "challenge"

    page_state = await page.evaluate(
        r"""() => {
          const visible = (node) => {
            const style = window.getComputedStyle(node);
            if (
              style.display === 'none'
              || style.visibility === 'hidden'
              || Number(style.opacity || '1') === 0
            ) return false;
            const rect = node.getBoundingClientRect();
            return (
              rect.width >= 20
              && rect.height >= 16
              && rect.bottom > 0
              && rect.right > 0
              && rect.top < window.innerHeight
              && rect.left < window.innerWidth
            );
          };
          const bodyText = String(document.body?.innerText || '').toLowerCase();
          const verifying = (
            bodyText.includes('verifying browser')
            || bodyText.includes('verify your browser')
            || bodyText.includes('checking your browser')
          );
          const authNodes = Array.from(document.querySelectorAll(
            'a[href*="/login"], button, [role="button"]'
          )).filter(visible);
          const authText = authNodes
            .map((node) => String(node.textContent || '').trim().toLowerCase())
            .filter(Boolean);
          const loginVisible = authText.some((value) =>
            value === 'log in'
            || value === 'login'
            || value === 'sign up'
            || value === 'signup'
          );
          return { verifying, loginVisible };
        }"""
    )
    if isinstance(page_state, dict):
        if bool(page_state.get("verifying")):
            return "challenge"
        if bool(page_state.get("loginVisible")):
            return "login"
    return None


async def startup_access_gate(page: Any) -> str | None:
    """Check Pinterest access before any Scout campaign is claimed or scanned."""
    try:
        response = await page.goto(
            "https://www.pinterest.com/",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        guard_pinterest_response(response)
    except PinterestAccessGateError as exc:
        return exc.gate
    await page.wait_for_timeout(2_500)
    return await access_gate(page)


async def loaded_pin_count(page: Any) -> int:
    value = await page.evaluate(
        r"""() => {
          const hrefs = new Set(
            Array.from(document.querySelectorAll('a[href*="/pin/"]'))
              .map((anchor) => anchor.href || anchor.getAttribute('href') || '')
              .filter(Boolean)
          );
          return hrefs.size;
        }"""
    )
    return int(value or 0)


async def wait_for_pin_growth(
    page: Any,
    *,
    previous_count: int,
    timeout_ms: int,
) -> int:
    deadline = time.monotonic() + (timeout_ms / 1000)
    latest = previous_count
    while time.monotonic() < deadline:
        latest = await loaded_pin_count(page)
        if latest > previous_count:
            return latest
        await page.wait_for_timeout(150)
    return latest


class CamClient:
    """Legacy one-campaign client kept for backwards-compatible Scout commands."""

    def __init__(self, base_url: str, campaign_id: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.campaign_id = campaign_id
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def task(self) -> dict[str, Any]:
        response = await self.client.get(
            "/api/v1/realistic-review-ugc/scout/" + self.campaign_id + "/task"
        )
        response.raise_for_status()
        return response.json()

    async def heartbeat(self, status: str) -> None:
        response = await self.client.post(
            "/api/v1/realistic-review-ugc/scout/" + self.campaign_id + "/heartbeat",
            json={"status": status},
        )
        response.raise_for_status()

    async def submit(self, rows: list[Candidate]) -> dict[str, Any]:
        response = await self.client.post(
            "/api/v1/realistic-review-ugc/scout/" + self.campaign_id + "/candidates",
            json={"items": [row.as_json() for row in rows]},
        )
        response.raise_for_status()
        return response.json()


class AutoScoutClient:
    def __init__(
        self,
        base_url: str,
        agent_id: str,
        token: str,
        *,
        machine_label: str,
    ):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.machine_label = machine_label
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(45.0, connect=10.0),
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        **kwargs: Any,
    ) -> httpx.Response:
        started = time.monotonic()
        try:
            response = await self.client.request(method, path, **kwargs)
        except Exception as exc:
            scout_debug_event(
                "cam_request_failed",
                operation=operation,
                method=method,
                path=path,
                duration_ms=max(
                    0,
                    round((time.monotonic() - started) * 1000),
                ),
                error_type=exc.__class__.__name__,
            )
            raise
        scout_debug_event(
            "cam_request",
            operation=operation,
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=max(
                0,
                round((time.monotonic() - started) * 1000),
            ),
        )
        return response

    async def heartbeat(
        self,
        status: str,
        *,
        run_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/heartbeat",
            operation="heartbeat",
            json={
                "status": status,
                "client_version": CLIENT_VERSION,
                "machine_label": self.machine_label,
                "run_id": run_id,
                "error_code": error_code,
            },
        )
        response.raise_for_status()
        return response.json()

    async def claim(self) -> dict[str, Any] | None:
        response = await self._request(
            "POST",
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/claim",
            operation="claim",
            headers={
                "X-Scout-Version": CLIENT_VERSION,
                "X-Scout-Machine": self.machine_label,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload:
            scout_debug_event(
                "claim_result",
                claimed=True,
                campaign_id=str(payload.get("campaign_id") or ""),
                run_id=str((payload.get("run") or {}).get("id") or ""),
                query=str(payload.get("query") or ""),
                progress=int(payload.get("progress") or 0),
                pipeline_count=int(payload.get("pipeline_count") or 0),
                target_count=int(payload.get("target_count") or 0),
            )
        else:
            scout_debug_event("claim_result", claimed=False)
        return payload or None

    async def diagnostics(self) -> dict[str, Any]:
        response = await self._request(
            "GET",
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/diagnostics",
            operation="diagnostics",
            headers={
                "X-Scout-Version": CLIENT_VERSION,
                "X-Scout-Machine": self.machine_label,
            },
        )
        response.raise_for_status()
        payload = response.json()
        campaigns = payload.get("campaigns") if isinstance(payload, dict) else None
        first = campaigns[0] if isinstance(campaigns, list) and campaigns else {}
        scout_debug_event(
            "idle_diagnostics",
            claimable=int(payload.get("claimable") or 0) if isinstance(payload, dict) else 0,
            server_duration_ms=int(payload.get("duration_ms") or 0) if isinstance(payload, dict) else 0,
            first_campaign_id=str(first.get("campaign_id") or "") if isinstance(first, dict) else "",
            first_campaign_reason=str(first.get("reason") or "") if isinstance(first, dict) else "",
            first_campaign_progress=int(first.get("progress") or 0) if isinstance(first, dict) else 0,
            first_campaign_pipeline=int(first.get("pipeline_count") or 0) if isinstance(first, dict) else 0,
            first_campaign_source_plan_ready=(
                first.get("source_plan_ready")
                if isinstance(first, dict)
                else None
            ),
            first_campaign_source_plan_statuses=(
                first.get("source_plan_statuses")
                if isinstance(first, dict)
                else None
            ),
        )
        return payload

    async def submit(
        self,
        run_id: str,
        rows: list[Candidate],
        *,
        source_query: str | None = None,
    ) -> dict[str, Any]:
        scout_debug_event(
            "candidate_submit_start",
            run_id=run_id,
            item_count=len(rows),
            source_query=source_query,
        )
        response = await self._request(
            "POST",
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/runs/{run_id}/candidates",
            operation="submit_candidates",
            json={
                "items": [row.as_json() for row in rows],
                "source_query": source_query,
            },
        )
        response.raise_for_status()
        return response.json()

    async def complete(
        self,
        run_id: str,
        status: str,
        *,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        scout_debug_event(
            "run_complete_start",
            run_id=run_id,
            status=status,
            error_code=error_code,
        )
        response = await self._request(
            "POST",
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/runs/{run_id}/complete",
            operation="complete_run",
            json={"status": status, "error_code": error_code},
        )
        response.raise_for_status()
        return response.json()


async def wait_for_manual_access(
    page: Any,
    client: AutoScoutClient,
    *,
    run_id: str,
    timeout_seconds: int,
) -> bool:
    gate = await access_gate(page)
    if gate is None:
        return True

    await client.heartbeat(
        "needs_login",
        run_id=run_id,
        error_code="pinterest_" + gate + "_required",
    )
    print(
        "Pinterest requires manual "
        + ("login" if gate == "login" else "challenge verification")
        + ". Resolve it in the open browser window. The Scout will resume automatically."
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        await page.wait_for_timeout(MANUAL_GATE_POLL_MS)
        gate = await access_gate(page)
        if gate is None:
            await client.heartbeat("busy", run_id=run_id)
            return True
    return False


def random_ms(bounds: tuple[int, int]) -> int:
    low, high = bounds
    return random.randint(low, high)


async def paced_wait(page: Any, bounds: tuple[int, int]) -> int:
    duration = random_ms(bounds)
    await page.wait_for_timeout(duration)
    return duration


async def paced_scroll(page: Any, pace: ScoutPace) -> int:
    steps = random.randint(*pace.scroll_steps_per_batch)
    total = 0
    for _ in range(steps):
        amount = random.randint(*pace.scroll_step_px)
        total += amount
        await page.mouse.wheel(0, amount)
        await paced_wait(page, pace.scroll_step_pause_ms)
    return total


def keyword_candidate_budgets(total_cap: int, query_count: int) -> list[int]:
    active_queries = min(
        max(0, int(query_count)),
        max(0, int(total_cap)),
        MAX_KEYWORDS_PER_RUN,
    )
    if active_queries <= 0:
        return []
    base, remainder = divmod(int(total_cap), active_queries)
    return [
        base + (1 if index < remainder else 0)
        for index in range(active_queries)
    ]


def task_search_queries(task: dict[str, Any]) -> list[str]:
    """Return source-plan context queries before adaptive/legacy campaign queries.

    Source-plan claims include the AI-derived product context for the exact
    embroidery image. Keep direct/adjacent scene hints at the front so an
    adaptive campaign refresh cannot silently make a generic historical query
    the first Pinterest search again.
    """
    raw_queries: list[Any] = []
    source_plan_id = str(task.get("source_plan_id") or "").strip()
    source_context = task.get("source_context")
    if source_plan_id and isinstance(source_context, dict):
        clusters = source_context.get("search_clusters")
        if isinstance(clusters, dict):
            # Round-robin high-value composition clusters so the four-keyword
            # per-run cap cannot starve hand-held/selfie references behind a
            # long list of generic direct queries.
            levels = (
                "selfie_wearing_hat",
                "hand_holding_hat",
                "direct",
                "adjacent",
                "text_match",
            )
            cluster_rows = {
                level: clusters.get(level)
                if isinstance(clusters.get(level), list)
                else []
                for level in levels
            }
            max_depth = max((len(values) for values in cluster_rows.values()), default=0)
            for index in range(max_depth):
                for level in levels:
                    values = cluster_rows[level]
                    if index < len(values):
                        raw_queries.append(values[index])

    configured = task.get("search_queries")
    if isinstance(configured, list):
        raw_queries.extend(configured)
    else:
        raw_queries.append(task.get("query"))

    search_queries: list[str] = []
    seen_queries: set[str] = set()
    for raw in raw_queries:
        value = str(raw or "").strip()
        key = value.casefold()
        if value and key not in seen_queries:
            seen_queries.add(key)
            search_queries.append(value)

    fallback = str(task.get("query") or "").strip()
    if not search_queries and fallback:
        search_queries.append(fallback)
    return search_queries


def related_seed_candidates(task: dict[str, Any]) -> list[Candidate]:
    seeds: list[Candidate] = []
    seen: set[str] = set()
    for raw in task.get("related_seeds") or []:
        if not isinstance(raw, dict):
            continue
        candidate = Candidate(
            str(raw.get("pin_url") or "").strip(),
            str(raw.get("image_url") or "").strip(),
            str(raw.get("alt_text") or "").strip() or None,
        )
        key = pin_history_key(candidate.pin_url)
        if (
            not key
            or key in seen
            or not allowed_pin(candidate.pin_url)
            or not allowed_image(candidate.image_url)
        ):
            continue
        seen.add(key)
        seeds.append(candidate)
    return seeds


async def scan_auto_run(
    page: Any,
    client: AutoScoutClient,
    task: dict[str, Any],
    *,
    login_wait_seconds: int,
    pace_name: str = "careful",
    detail_page: Any | None = None,
    detail_concurrency: int = PIN_DETAIL_CONCURRENCY,
    history: ScoutHistory | None = None,
) -> None:
    pace = SCOUT_PACES.get(pace_name, SCOUT_PACES["careful"])
    run_id = str(task["run"]["id"])
    campaign_id = str(task["campaign_id"])
    source_plan_id = str(task.get("source_plan_id") or "").strip()
    source_relative_path = str(task.get("source_relative_path") or "").strip()
    source_name = str(task.get("source_name") or "").strip()
    history_keys = list(dict.fromkeys(
        key for key in (campaign_id, source_plan_id) if key
    ))
    related_history_key = campaign_id
    persistent_seen: set[str] = set()
    persistent_seen_assets: set[str] = set()
    if history:
        for history_key in history_keys:
            persistent_seen.update(history.seen_pin_keys(history_key))
            persistent_seen_assets.update(history.seen_asset_keys(history_key))

    def remember_history(rows: list[Candidate]) -> None:
        if not history or not rows:
            return
        for history_key in history_keys:
            history.remember(history_key, rows)
        persistent_seen.update(
            pin_history_key(row.pin_url) for row in rows if pin_history_key(row.pin_url)
        )
        persistent_seen_assets.update(
            pinimg_asset_key(row.image_url)
            for row in rows
            if pinimg_asset_key(row.image_url)
        )

    search_queries = task_search_queries(task)
    if not search_queries:
        search_queries = [str(task["query"])]

    await client.heartbeat("busy", run_id=run_id)
    last_heartbeat = time.monotonic()

    async def heartbeat_if_due(*, force: bool = False) -> None:
        nonlocal last_heartbeat
        now = time.monotonic()
        if force or now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            await client.heartbeat("busy", run_id=run_id)
            last_heartbeat = now

    seen_pins: set[str] = set()
    seen_assets: set[str] = set()
    target = int(task["target_count"])
    max_scroll_batches = int(task["max_scroll_batches"])
    progress = int(task.get("progress") or 0)
    pipeline_count = int(task.get("pipeline_count") or progress)
    remaining_pipeline_budget = max(0, target - pipeline_count)
    run_candidate_cap = min(
        QUALITY_FIRST_RUN_CANDIDATE_CAP,
        remaining_pipeline_budget,
    )
    created_this_run = 0
    if run_candidate_cap <= 0:
        await client.complete(run_id, "completed")
        return

    approved_seeds = related_seed_candidates(task)
    expanded_seed_keys = (
        history.expanded_seed_keys(related_history_key) if history else set()
    )
    related_page = detail_page or page
    for seed in approved_seeds:
        seed_key = pin_history_key(seed.pin_url)
        if seed_key in expanded_seed_keys:
            continue

        await heartbeat_if_due(force=True)
        related_rows = await extract_related_candidates(
            related_page,
            seed,
            pace=pace,
            limit=PIN_RELATED_SCAN_LIMIT,
        )
        unseen_related = [
            row
            for row in related_rows
            if (
                pin_history_key(row.pin_url) not in persistent_seen
                and pinimg_asset_key(row.image_url) not in persistent_seen_assets
            )
        ]
        filtered_related = [
            row
            for row in unseen_related
            if synthetic_metadata_reason(row) is not None
        ]
        fresh_related, related_metadata_filtered = quality_prefilter(
            unseen_related
        )
        if filtered_related:
            remember_history(filtered_related)

        related_budget = max(0, target - pipeline_count)
        selected_related = fresh_related[:related_budget]
        print(
            "campaign="
            + campaign_id
            + " related_seed="
            + seed.pin_url
            + " related_scanned="
            + str(len(related_rows))
            + "/"
            + str(PIN_RELATED_SCAN_LIMIT)
            + " related_unseen="
            + str(len(unseen_related))
            + " related_quality_candidates="
            + str(len(fresh_related))
            + " related_metadata_filtered="
            + str(related_metadata_filtered)
        )

        related_created = 0
        related_existing = 0
        for start in range(0, len(selected_related), pace.submit_batch_size):
            chunk = selected_related[start:start + pace.submit_batch_size]
            if not chunk:
                continue
            resolved_chunk = await resolve_pin_details(
                page,
                chunk,
                detail_page=related_page,
                concurrency=1,
            )
            result = await client.submit(
                run_id,
                resolved_chunk,
                source_query=None,
            )
            remember_history(resolved_chunk)
            related_created += int(result.get("created") or 0)
            related_existing += int(result.get("existing") or 0)
            progress = int(result.get("progress") or 0)
            pipeline_count = int(result.get("pipeline_count") or progress)
            await heartbeat_if_due()
            if (
                result.get("campaign_status") != "running"
                or progress >= target
                or pipeline_count >= target
            ):
                if history and len(selected_related) == len(fresh_related):
                    history.remember_expanded_seed(
                        related_history_key,
                        seed.pin_url,
                    )
                await client.complete(run_id, "completed")
                return
            await paced_wait(page, pace.submit_pause_ms)

        if history and len(selected_related) == len(fresh_related):
            history.remember_expanded_seed(
                related_history_key,
                seed.pin_url,
            )
        print(
            "campaign="
            + campaign_id
            + " related_seed_complete created="
            + str(related_created)
            + " existing="
            + str(related_existing)
            + " pipeline="
            + str(pipeline_count)
            + "/"
            + str(target)
        )
        # Expand at most one approved Pin per run to keep Pinterest access
        # low-footprint. Keyword discovery continues in the same run.
        break

    search_queries = search_queries[
        :min(len(search_queries), MAX_KEYWORDS_PER_RUN, run_candidate_cap)
    ]
    keyword_budgets = keyword_candidate_budgets(
        run_candidate_cap,
        len(search_queries),
    )
    if source_plan_id:
        print(
            "source_plan="
            + source_plan_id
            + " source="
            + (source_relative_path or source_name or "unknown")
            + " campaign="
            + campaign_id
            + " target="
            + str(target)
            + " refs"
        )
    print(
        "campaign="
        + str(task["campaign_id"])
        + " ranked_keywords="
        + " | ".join(
            f"{query} (cap={keyword_budgets[index]})"
            for index, query in enumerate(search_queries)
        )
    )

    for query_index, raw_query in enumerate(search_queries):
        keyword_candidate_cap = keyword_budgets[query_index]
        created_for_keyword = 0
        search_query = quality_search_query(raw_query)
        query = quote_plus(search_query)
        url = "https://www.pinterest.com/search/pins/?q=" + query
        await heartbeat_if_due(force=True)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        guard_pinterest_response(response)
        loaded_count = await wait_for_pin_growth(
            page,
            previous_count=0,
            timeout_ms=INITIAL_RESULTS_TIMEOUT_MS,
        )
        initial_dwell = await paced_wait(page, pace.initial_dwell_ms)

        gate = await access_gate(page)
        if gate is not None:
            raise PinterestAccessGateError(gate)

        scout_debug_event(
            "pinterest_query_start",
            campaign_id=str(task["campaign_id"]),
            run_id=run_id,
            keyword_index=query_index + 1,
            keyword_count=len(search_queries),
            raw_query=raw_query,
            pinterest_query=search_query,
            pace=pace.name,
            initial_dwell_ms=initial_dwell,
        )
        print(
            "campaign="
            + str(task["campaign_id"])
            + " keyword="
            + str(query_index + 1)
            + "/"
            + str(len(search_queries))
            + " query="
            + raw_query
            + " pinterest_query="
            + search_query
            + " pace="
            + pace.name
            + " initial_dwell_ms="
            + str(initial_dwell)
        )

        max_batch_attempts = max_scroll_batches + HISTORY_REPLAY_EXTRA_SCROLL_BATCHES
        discovery_batches = 0
        for batch in range(max_batch_attempts):
            await heartbeat_if_due()
            inspect_dwell = await paced_wait(page, pace.inspect_dwell_ms)
            visible = await extract_visible(page)
            visible_once: list[Candidate] = []
            for row in visible:
                pin_key = pin_history_key(row.pin_url)
                asset_key = pinimg_asset_key(row.image_url)
                if pin_key in seen_pins or (asset_key and asset_key in seen_assets):
                    continue
                seen_pins.add(pin_key)
                if asset_key:
                    seen_assets.add(asset_key)
                visible_once.append(row)
            legacy_asset_backfill = [
                row
                for row in visible_once
                if (
                    pin_history_key(row.pin_url) in persistent_seen
                    and pinimg_asset_key(row.image_url)
                    and pinimg_asset_key(row.image_url) not in persistent_seen_assets
                )
            ]
            if legacy_asset_backfill:
                remember_history(legacy_asset_backfill)
            persistent_skipped = sum(
                1
                for row in visible_once
                if (
                    pin_history_key(row.pin_url) in persistent_seen
                    or pinimg_asset_key(row.image_url) in persistent_seen_assets
                )
            )
            unseen = [
                row
                for row in visible_once
                if (
                    pin_history_key(row.pin_url) not in persistent_seen
                    and pinimg_asset_key(row.image_url) not in persistent_seen_assets
                )
            ]
            if unseen:
                discovery_batches += 1
            elif visible_once or persistent_skipped:
                scout_debug_event(
                    "pinterest_history_replay_batch",
                    campaign_id=str(task["campaign_id"]),
                    run_id=run_id,
                    keyword_index=query_index + 1,
                    batch_index=batch + 1,
                    persistent_skipped=persistent_skipped,
                    replay_budget=HISTORY_REPLAY_EXTRA_SCROLL_BATCHES,
                )
            filtered_rows = [
                row for row in unseen
                if synthetic_metadata_reason(row) is not None
            ]
            fresh, metadata_filtered = quality_prefilter(unseen)
            if filtered_rows:
                remember_history(filtered_rows)
            scout_debug_event(
                "pinterest_batch_inspected",
                campaign_id=str(task["campaign_id"]),
                run_id=run_id,
                keyword_index=query_index + 1,
                keyword_count=len(search_queries),
                batch_index=batch + 1,
                batch_count=max_batch_attempts,
                discovery_batches=discovery_batches,
                discovery_batch_budget=max_scroll_batches,
                inspect_dwell_ms=inspect_dwell,
                visible=len(visible),
                unseen=len(unseen),
                persistent_skipped=persistent_skipped,
                quality_candidates=len(fresh),
                metadata_filtered=metadata_filtered,
            )
            print(
                "campaign="
                + str(task["campaign_id"])
                + " keyword="
                + str(query_index + 1)
                + "/"
                + str(len(search_queries))
                + " batch_attempt="
                + str(batch + 1)
                + "/"
                + str(max_batch_attempts)
                + " discovery_batches="
                + str(discovery_batches)
                + "/"
                + str(max_scroll_batches)
                + " inspect_dwell_ms="
                + str(inspect_dwell)
                + " visible="
                + str(len(visible))
                + " unseen="
                + str(len(unseen))
                + " persistent_skipped="
                + str(persistent_skipped)
                + " quality_candidates="
                + str(len(fresh))
            )
            if metadata_filtered:
                print(
                    "campaign="
                    + str(task["campaign_id"])
                    + " keyword="
                    + str(query_index + 1)
                    + "/"
                    + str(len(search_queries))
                    + " batch="
                    + str(batch + 1)
                    + " metadata_filtered="
                    + str(metadata_filtered)
                )

            keyword_budget_reached = False
            for start in range(0, len(fresh), pace.submit_batch_size):
                remaining_run_budget = run_candidate_cap - created_this_run
                remaining_keyword_budget = keyword_candidate_cap - created_for_keyword
                if remaining_run_budget <= 0:
                    await client.complete(run_id, "completed")
                    return
                if remaining_keyword_budget <= 0:
                    keyword_budget_reached = True
                    break
                chunk = fresh[
                    start:start + min(
                        pace.submit_batch_size,
                        remaining_run_budget,
                        remaining_keyword_budget,
                    )
                ]
                if not chunk:
                    continue
                resolved_chunk = await resolve_pin_details(
                    page,
                    chunk,
                    detail_page=detail_page,
                    concurrency=1,
                )
                resolved_by_pin = {
                    pin_history_key(row.pin_url): row
                    for row in resolved_chunk
                    if pin_history_key(row.pin_url)
                }
                upgraded = sum(
                    1
                    for before in chunk
                    if pin_history_key(before.pin_url) in resolved_by_pin
                    and before.image_url
                    != resolved_by_pin[pin_history_key(before.pin_url)].image_url
                )
                resolved_pin_keys = set(resolved_by_pin)
                video_filtered = [
                    row for row in chunk
                    if pin_history_key(row.pin_url) not in resolved_pin_keys
                ]
                if video_filtered:
                    remember_history(video_filtered)
                    scout_debug_event(
                        "video_pins_filtered",
                        campaign_id=str(task["campaign_id"]),
                        run_id=run_id,
                        filtered=len(video_filtered),
                    )
                if upgraded:
                    scout_debug_event(
                        "pin_detail_upgraded",
                        campaign_id=str(task["campaign_id"]),
                        run_id=run_id,
                        upgraded=upgraded,
                        resolved=len(resolved_chunk),
                    )
                    print(
                        "campaign="
                        + str(task["campaign_id"])
                        + " pin_detail_upgraded="
                        + str(upgraded)
                        + "/"
                        + str(len(resolved_chunk))
                    )
                result = await client.submit(
                    run_id,
                    resolved_chunk,
                    source_query=raw_query,
                )
                remember_history(resolved_chunk)
                created_now = int(result.get("created") or 0)
                created_this_run += created_now
                created_for_keyword += created_now
                progress = int(result.get("progress") or 0)
                pipeline_count = int(result.get("pipeline_count") or progress)
                scout_debug_event(
                    "pinterest_submit_result",
                    campaign_id=str(task["campaign_id"]),
                    run_id=run_id,
                    source_query=raw_query,
                    keyword_index=query_index + 1,
                    batch_index=batch + 1,
                    submitted=len(resolved_chunk),
                    created=int(result.get("created") or 0),
                    existing=int(result.get("existing") or 0),
                    new_this_run=created_this_run,
                    run_candidate_cap=run_candidate_cap,
                    progress=progress,
                    pipeline_count=pipeline_count,
                    target=target,
                )
                print(
                    "campaign="
                    + str(task["campaign_id"])
                    + " keyword="
                    + str(query_index + 1)
                    + "/"
                    + str(len(search_queries))
                    + " batch_attempt="
                    + str(batch + 1)
                    + "/"
                    + str(max_batch_attempts)
                    + " submitted="
                    + str(len(resolved_chunk))
                    + " created="
                    + str(result.get("created") or 0)
                    + " existing="
                    + str(result.get("existing") or 0)
                    + " new_this_run="
                    + str(created_this_run)
                    + "/"
                    + str(run_candidate_cap)
                    + " target_progress="
                    + str(progress)
                    + "/"
                    + str(target)
                    + " pipeline="
                    + str(pipeline_count)
                    + "/"
                    + str(target)
                )
                if (
                    result.get("campaign_status") != "running"
                    or progress >= target
                    or pipeline_count >= target
                    or created_this_run >= run_candidate_cap
                ):
                    await client.complete(run_id, "completed")
                    return
                submit_pause = await paced_wait(page, pace.submit_pause_ms)
                print(
                    "campaign="
                    + str(task["campaign_id"])
                    + " submit_pause_ms="
                    + str(submit_pause)
                )

            if keyword_budget_reached or created_for_keyword >= keyword_candidate_cap:
                break
            if discovery_batches >= max_scroll_batches:
                break

            gate = await access_gate(page)
            if gate is not None:
                access_ready = await wait_for_manual_access(
                    page,
                    client,
                    run_id=run_id,
                    timeout_seconds=login_wait_seconds,
                )
                if not access_ready:
                    await client.complete(
                        run_id,
                        "needs_login",
                        error_code="pinterest_access_gate_timeout",
                    )
                    return

            if (
                batch + 1 < max_batch_attempts
                and discovery_batches < max_scroll_batches
            ):
                before_scroll_count = max(
                    loaded_count,
                    await loaded_pin_count(page),
                )
                scrolled_px = await paced_scroll(page, pace)
                loaded_count = await wait_for_pin_growth(
                    page,
                    previous_count=before_scroll_count,
                    timeout_ms=SCROLL_RESULTS_TIMEOUT_MS,
                )
                print(
                    "campaign="
                    + str(task["campaign_id"])
                    + " keyword="
                    + str(query_index + 1)
                    + "/"
                    + str(len(search_queries))
                    + " batch="
                    + str(batch + 1)
                    + " gradual_scroll_px="
                    + str(scrolled_px)
                    + " loaded_pins="
                    + str(loaded_count)
                )

        if query_index + 1 < len(search_queries):
            keyword_pause = await paced_wait(page, pace.keyword_pause_ms)
            print(
                "campaign="
                + str(task["campaign_id"])
                + " next_keyword_pause_ms="
                + str(keyword_pause)
            )

    await client.complete(run_id, "completed")


async def scan_page(
    page: Any,
    client: CamClient,
    task: dict[str, Any],
    *,
    detail_page: Any | None = None,
    detail_concurrency: int = PIN_DETAIL_CONCURRENCY,
) -> None:
    """Legacy bounded one-shot campaign scan."""
    query = quote_plus(str(task["query"]))
    url = "https://www.pinterest.com/search/pins/?q=" + query
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    guard_pinterest_response(response)
    await page.wait_for_timeout(2_500)

    gate = await access_gate(page)
    if gate is not None:
        await client.heartbeat("needs_login")
        raise RuntimeError(
            "Pinterest requires manual login/challenge resolution for this persistent profile. "
            "Use Auto Scout v10 after resolving access manually."
        )

    seen_pins: set[str] = set()
    target = int(task["target_count"])
    max_scroll_batches = int(task["max_scroll_batches"])
    auto_import = bool(task["auto_import"])
    starting = int(task["drive_ready"] if auto_import else task["approved"])

    for batch in range(max_scroll_batches):
        await client.heartbeat("busy")
        visible = await extract_visible(page)
        fresh = [
            row for row in visible
            if row.pin_url not in seen_pins
        ]
        for row in fresh:
            seen_pins.add(row.pin_url)

        for start in range(0, len(fresh), 25):
            chunk = fresh[start:start + 25]
            if not chunk:
                continue
            resolved_chunk = await resolve_pin_details(
                page,
                chunk,
                detail_page=detail_page,
                concurrency=1,
            )
            result = await client.submit(resolved_chunk)
            latest = await client.task()
            starting = int(latest["drive_ready"] if auto_import else latest["approved"])
            print(
                "batch="
                + str(batch + 1)
                + "/"
                + str(max_scroll_batches)
                + " submitted="
                + str(len(resolved_chunk))
                + " created="
                + str(result.get("created") or 0)
                + " target_progress="
                + str(starting)
            )
            if latest["status"] != "running" or starting >= target:
                await client.heartbeat("ready")
                return

        await page.mouse.wheel(0, 2600)
        await page.wait_for_timeout(1_500)

    await client.heartbeat("ready")


async def launch_context(args: argparse.Namespace) -> tuple[Any, Any]:
    from playwright.async_api import async_playwright

    profile = Path(args.profile_dir).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    options: dict[str, Any] = {
        "user_data_dir": str(profile),
        "headless": args.headless,
        "viewport": {"width": 1440, "height": 1000},
    }
    chrome = resolve_chrome_executable(args.chrome_executable)
    if chrome:
        options["executable_path"] = chrome
        print("Using local Chrome: " + chrome)
    else:
        print("Local Chrome not found; using Playwright Chromium.")

    try:
        context = await playwright.chromium.launch_persistent_context(**options)
        return playwright, context
    except Exception as first_error:
        if sys.platform != "win32" or not _looks_like_profile_launch_collision(first_error):
            await playwright.stop()
            raise

        print(
            "Chrome closed during Scout startup. "
            "Checking the dedicated Pinterest profile for an existing/stale owner..."
        )
        try:
            _recover_windows_scout_profile(profile)
        except Exception:
            await playwright.stop()
            raise

        await asyncio.sleep(0.75)
        try:
            context = await playwright.chromium.launch_persistent_context(**options)
            print("Pinterest Scout Chrome recovered and started successfully.")
            return playwright, context
        except Exception as retry_error:
            await playwright.stop()
            raise RuntimeError(
                "Pinterest Scout Chrome could not start after automatic profile recovery. "
                "Only the dedicated Scout profile was touched. Close any Chrome window using "
                + str(profile)
                + " and run START_SCOUT.bat again."
            ) from retry_error


async def run_legacy(args: argparse.Namespace) -> None:
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    log_path = configure_scout_debug_log(profile_dir)
    print("Pinterest Scout debug log: " + str(log_path))
    client = CamClient(args.base_url, args.campaign_id, args.token)
    playwright = None
    context = None
    try:
        task = await client.task()
        if task["status"] != "running":
            print("Campaign status is " + str(task["status"]) + "; nothing to scan.")
            return
        playwright, context = await launch_context(args)
        page = context.pages[0] if context.pages else await context.new_page()
        for stale_page in list(context.pages[1:]):
            try:
                await stale_page.close()
            except Exception:
                pass
        detail_page = await context.new_page()
        await scan_page(
            page,
            client,
            task,
            detail_page=detail_page,
            detail_concurrency=1,
        )
    except Exception:
        try:
            await client.heartbeat("error")
        except Exception:
            pass
        raise
    finally:
        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()
        await client.close()


def idle_diagnostic_message(payload: dict[str, Any]) -> str:
    campaigns = payload.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns:
        return "Scout idle: no campaigns are available for this agent."

    campaign = campaigns[0]
    reason = str(campaign.get("reason") or "waiting")
    reason_labels = {
        "pipeline_full": "pipeline is full; waiting for analysis/import workers",
        "target_reached": "target has been reached",
        "scheduled_later": "next scan is scheduled later",
        "campaign_leased": "campaign is currently leased by another run",
        "auto_scout_disabled": "Auto Scout is disabled for this campaign",
        "campaign_not_running": "campaign is not running",
        "source_plan_not_ready": "source plan is waiting for AI Context analysis",
        "claimable": "campaign is claimable; retrying claim",
    }
    name = str(campaign.get("name") or campaign.get("campaign_id") or "campaign")
    target = int(campaign.get("target_count") or 0)
    progress = int(campaign.get("progress") or 0)
    pipeline = int(campaign.get("pipeline_count") or 0)
    counts = campaign.get("counts") if isinstance(campaign.get("counts"), dict) else {}
    active_counts = ", ".join(
        f"{key}={value}"
        for key, value in counts.items()
        if isinstance(value, int) and value > 0
    )
    message = (
        f"Scout idle: {name} — {reason_labels.get(reason, reason)}. "
        f"progress={progress}/{target} pipeline={pipeline}/{target}"
    )
    if active_counts:
        message += " [" + active_counts + "]"
    if reason == "scheduled_later" and campaign.get("scan_next_at"):
        message += " next_scan=" + str(campaign["scan_next_at"])
    return message


async def run_agent(args: argparse.Namespace) -> None:
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    log_path = configure_scout_debug_log(profile_dir, scout_type="review")
    configure_scout_remote_log(
        base_url=args.base_url,
        agent_id=args.agent_id,
        token=args.token,
    )
    print("Pinterest Scout debug log: " + str(log_path))
    print("Remote Scout log      : API enabled · retention 5 days")
    machine_label = args.machine_label or socket.gethostname()
    scout_debug_event(
        "review_scout_remote_logging_enabled",
        retention_days=5,
    )
    scout_debug_event(
        "agent_start",
        agent_id=args.agent_id,
        machine_label=machine_label,
        base_url=args.base_url,
        pace=args.pace,
    )
    client = AutoScoutClient(
        args.base_url,
        args.agent_id,
        args.token,
        machine_label=machine_label,
    )
    playwright = None
    context = None
    page = None
    detail_page = None
    browser_started_at = 0.0
    browser_runtime_failures = 0
    runtime_error_streak = 0
    successful_runs_since_restart = 0
    history = ScoutHistory(
        profile_dir / SCOUT_HISTORY_FILENAME
    )
    idle_failures = 0
    last_idle_diagnostic_at = 0.0

    async def open_browser_runtime() -> tuple[Any, Any, Any, Any]:
        next_playwright, next_context = await launch_context(args)
        next_page = (
            next_context.pages[0]
            if next_context.pages
            else await next_context.new_page()
        )
        for stale_page in list(next_context.pages[1:]):
            try:
                await stale_page.close()
            except Exception:
                pass
        next_detail_page = await next_context.new_page()
        return next_playwright, next_context, next_page, next_detail_page

    async def close_browser_runtime() -> None:
        nonlocal playwright, context, page, detail_page
        current_context = context
        current_playwright = playwright
        page = None
        detail_page = None
        context = None
        playwright = None
        if current_context is not None:
            try:
                await asyncio.wait_for(current_context.close(), timeout=10)
            except Exception:
                pass
        if current_playwright is not None:
            try:
                await asyncio.wait_for(current_playwright.stop(), timeout=10)
            except Exception:
                pass

    async def recycle_browser(reason: str) -> None:
        nonlocal playwright, context, page, detail_page
        nonlocal browser_started_at, browser_runtime_failures
        scout_debug_event(
            "browser_recycle_started",
            reason=reason,
            runtime_failure_streak=browser_runtime_failures,
        )
        await close_browser_runtime()
        playwright, context, page, detail_page = await open_browser_runtime()
        browser_started_at = time.monotonic()
        browser_runtime_failures = 0
        scout_debug_event("browser_recycle_completed", reason=reason)

    async def ensure_startup_login() -> None:
        nonlocal playwright, context, page, detail_page, browser_started_at

        if not pinterest_login_ready(profile_dir):
            await client.heartbeat(
                "needs_login",
                error_code="pinterest_startup_manual_login_required",
            )
            scout_debug_event(
                "startup_manual_login_required",
                scout_type="review",
                profile_dir=str(profile_dir),
            )
            print("")
            print(
                "Pinterest login has not been verified for this Review Scout profile. "
                "No campaign will be claimed before manual sign-in completes."
            )
            await asyncio.to_thread(
                bootstrap_login,
                str(profile_dir),
                args.chrome_executable,
            )

        while True:
            if page is None:
                playwright, context, page, detail_page = await open_browser_runtime()
                browser_started_at = time.monotonic()
            gate = await startup_access_gate(page)
            if gate is None:
                mark_pinterest_login_ready(profile_dir)
                await client.heartbeat("ready")
                scout_debug_event("startup_access_ready", scout_type="review")
                print("Pinterest login verified. Auto Scout can start.")
                return

            clear_pinterest_login_ready(profile_dir)
            await client.heartbeat(
                "needs_login",
                error_code="pinterest_startup_" + gate + "_required",
            )
            scout_debug_event(
                "startup_access_blocked",
                scout_type="review",
                gate=gate,
            )
            print("")
            print(
                "Pinterest is not ready for scouting yet ("
                + gate
                + "). No campaign will be claimed."
            )
            print(
                "Opening normal Chrome with the dedicated Scout profile. "
                "Use Pinterest email/password, finish verification/login, then close that "
                "Chrome window. Scout will verify the saved session and start automatically."
            )
            await close_browser_runtime()
            await asyncio.to_thread(
                bootstrap_login,
                str(profile_dir),
                args.chrome_executable,
            )

    try:
        await ensure_startup_login()
        print(
            "Pinterest Auto Scout online. agent="
            + args.agent_id
            + " machine="
            + machine_label
            + " pace="
            + args.pace
            + " detail_mode=single-reused-tab"
        )
        while True:
            try:
                task = await client.claim()
                idle_failures = 0
                if task is None:
                    if args.once:
                        return
                    now = time.monotonic()
                    if (
                        now - last_idle_diagnostic_at
                        >= IDLE_DIAGNOSTIC_INTERVAL_SECONDS
                    ):
                        try:
                            print(idle_diagnostic_message(await client.diagnostics()))
                        except Exception as exc:
                            scout_debug_event(
                                "idle_diagnostics_failed",
                                error_type=exc.__class__.__name__,
                            )
                            print(
                                "Scout idle: no campaign claimed; diagnostics unavailable: "
                                + exc.__class__.__name__
                            )
                        last_idle_diagnostic_at = now
                    await asyncio.sleep(args.poll_interval_seconds)
                    continue
                try:
                    if _browser_session_needs_recycle(
                        page,
                        detail_page,
                        browser_started_at,
                    ):
                        await recycle_browser("session_age_or_closed")
                        await client.heartbeat("ready")
                    await scan_auto_run(
                        page,
                        client,
                        task,
                        login_wait_seconds=args.login_wait_seconds,
                        pace_name=args.pace,
                        detail_page=detail_page,
                        detail_concurrency=1,
                        history=history,
                    )
                    browser_runtime_failures = 0
                    runtime_error_streak = 0
                    successful_runs_since_restart += 1
                except PinterestAccessGateError as exc:
                    await client.complete(
                        str(task["run"]["id"]),
                        "needs_login",
                        error_code="pinterest_access_gate_" + exc.gate,
                    )
                    print(
                        "Pinterest access gate detected; run stopped for manual resolution. "
                        + "Scout will pause for "
                        + str(PIN_ACCESS_GATE_COOLDOWN_SECONDS)
                        + "s before claiming any new campaign."
                    )
                    if args.once:
                        return
                    await asyncio.sleep(PIN_ACCESS_GATE_COOLDOWN_SECONDS)
                    continue
                except PinterestRateLimitedError:
                    await client.complete(
                        str(task["run"]["id"]),
                        "failed",
                        error_code="pinterest_rate_limited_429",
                    )
                    print(
                        "Pinterest rate limit detected; pausing Scout for "
                        + str(PIN_RATE_LIMIT_COOLDOWN_SECONDS)
                        + "s before any new claim."
                    )
                    if args.once:
                        return
                    await asyncio.sleep(PIN_RATE_LIMIT_COOLDOWN_SECONDS)
                    continue
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in {401, 403}:
                        raise
                    try:
                        await client.complete(
                            str(task["run"]["id"]),
                            "failed",
                            error_code="cam_http_" + str(status),
                        )
                    except Exception:
                        pass
                    raise
                except Exception:
                    try:
                        await client.complete(
                            str(task["run"]["id"]),
                            "failed",
                            error_code="pinterest_scan_failed",
                        )
                    except Exception:
                        pass
                    raise
                if args.once:
                    return
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                idle_failures += 1
                delay = min(
                    60,
                    max(args.poll_interval_seconds, 2 ** min(idle_failures, 6)),
                )
                scout_debug_event(
                    "cam_connection_retry",
                    error_type=exc.__class__.__name__,
                    failure_streak=idle_failures,
                    retry_delay_seconds=delay,
                )
                print(
                    "CAM connection unavailable; retrying in "
                    + str(delay)
                    + "s: "
                    + exc.__class__.__name__
                )
                await asyncio.sleep(delay)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 403}:
                    print(
                        "Scout Agent credentials were rejected. Create a new agent pairing "
                        "in Realistic Review UGC and restart this process."
                    )
                    raise
                idle_failures += 1
                delay = min(60, 2 ** min(idle_failures, 6))
                scout_debug_event(
                    "cam_http_retry",
                    status_code=exc.response.status_code,
                    failure_streak=idle_failures,
                    retry_delay_seconds=delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                idle_failures += 1
                browser_runtime_failures += 1
                runtime_error_streak += 1
                try:
                    await client.heartbeat(
                        "error",
                        error_code="scout_runtime_error",
                    )
                except Exception:
                    pass
                delay = min(60, max(5, 2 ** min(idle_failures, 6)))
                recycle_needed = (
                    _looks_like_browser_runtime_failure(exc)
                    or browser_runtime_failures
                    >= BROWSER_RUNTIME_FAILURE_RECYCLE_THRESHOLD
                )
                scout_debug_event(
                    "scout_runtime_retry",
                    error_type=exc.__class__.__name__,
                    failure_streak=idle_failures,
                    runtime_error_streak=runtime_error_streak,
                    browser_failure_streak=browser_runtime_failures,
                    browser_recycle=recycle_needed,
                    retry_delay_seconds=delay,
                )
                if args.once:
                    raise
                if runtime_error_streak >= SCOUT_RUNTIME_ERRORS_BEFORE_RESTART:
                    scout_debug_event(
                        "scout_restart_requested",
                        scout_type="review",
                        error_code="review_scout_runtime_error_threshold",
                        runtime_error_streak=runtime_error_streak,
                        last_error_type=exc.__class__.__name__,
                        healthy_progress=successful_runs_since_restart > 0,
                    )
                    print(
                        "Review Scout hit "
                        + str(runtime_error_streak)
                        + " unexpected runtime errors; restarting the Scout runtime."
                    )
                    raise ScoutRestartRequested(
                        "review_scout_runtime_error_threshold",
                        healthy_progress=successful_runs_since_restart > 0,
                        last_error_type=exc.__class__.__name__,
                    ) from exc
                if recycle_needed:
                    reason = (
                        "browser_runtime_failure"
                        if _looks_like_browser_runtime_failure(exc)
                        else "runtime_failure_threshold"
                    )
                    try:
                        await recycle_browser(reason)
                        await client.heartbeat("ready")
                        print(
                            "Scout browser recovered automatically after "
                            + exc.__class__.__name__
                            + "; retrying in "
                            + str(delay)
                            + "s."
                        )
                    except Exception as recycle_error:
                        scout_debug_event(
                            "browser_recycle_failed",
                            reason=reason,
                            error_type=recycle_error.__class__.__name__,
                        )
                        print(
                            "Scout browser recycle failed; will retry recovery on the next "
                            "claimed run: "
                            + recycle_error.__class__.__name__
                        )
                else:
                    print(
                        "Scout run failed; retrying with the current browser in "
                        + str(delay)
                        + "s: "
                        + exc.__class__.__name__
                    )
                await asyncio.sleep(delay)
    finally:
        await close_browser_runtime()
        shutdown_scout_remote_log(timeout_seconds=3.0)
        await client.close()


def resolve_token(args: argparse.Namespace) -> str:
    token = str(args.token or os.getenv("RRUGC_SCOUT_TOKEN") or "").strip()
    if not token:
        raise SystemExit(
            "Scout token is required via --token or RRUGC_SCOUT_TOKEN."
        )
    return token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Realistic Review UGC Pinterest Browser Scout"
    )
    parser.add_argument("--base-url", default="")
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument(
        "--agent-id",
        help="Auto Scout agent ID. Runs quality-first and claims campaigns automatically.",
    )
    mode.add_argument(
        "--campaign-id",
        help="Legacy one-campaign mode.",
    )
    parser.add_argument("--token", default="")
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--chrome-executable", default="")
    parser.add_argument(
        "--bootstrap-login",
        action="store_true",
        help=(
            "Open normal local Chrome with the persistent Scout profile for manual "
            "Pinterest sign-in, then exit after Chrome is closed. Use this when Google "
            "refuses sign-in inside an automated browser."
        ),
    )
    parser.add_argument("--machine-label", default="")
    parser.add_argument(
        "--pace",
        choices=tuple(SCOUT_PACES),
        default="careful",
        help=(
            "Browser pacing profile. 'careful' is the default and uses longer "
            "dwell times, smaller gradual scrolls, smaller submit batches, and "
            "longer pauses between keywords."
        ),
    )
    parser.add_argument(
        "--detail-concurrency",
        type=int,
        default=PIN_DETAIL_CONCURRENCY,
        help=(
            "Compatibility option. Low-footprint mode always forces one reusable "
            "Pinterest detail tab, regardless of the supplied value."
        ),
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=5,
        help="Auto Scout idle claim interval.",
    )
    parser.add_argument(
        "--login-wait-seconds",
        type=int,
        default=1800,
        help="How long to keep the browser open for manual Pinterest login/challenge resolution.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim at most one Auto Scout task, useful for diagnostics.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Not recommended because Pinterest login/challenges must be resolved manually.",
    )
    args = parser.parse_args()
    if not 1 <= args.detail_concurrency <= 5:
        parser.error("--detail-concurrency must be between 1 and 5")
    if args.poll_interval_seconds < 2:
        parser.error("--poll-interval-seconds must be at least 2")
    if args.login_wait_seconds < 60:
        parser.error("--login-wait-seconds must be at least 60")
    if args.bootstrap_login:
        if args.agent_id or args.campaign_id or args.base_url:
            parser.error("--bootstrap-login only needs --profile-dir and optional --chrome-executable")
        return args
    if not args.base_url:
        parser.error("--base-url is required unless --bootstrap-login is used")
    if not args.agent_id and not args.campaign_id:
        parser.error("--agent-id or --campaign-id is required unless --bootstrap-login is used")
    args.token = resolve_token(args)
    return args


async def supervise_review_scout(args: argparse.Namespace) -> None:
    restart_attempts = 0
    machine_label = args.machine_label or socket.gethostname()
    while True:
        try:
            await run_agent(args)
            return
        except ScoutRestartRequested as exc:
            if exc.healthy_progress:
                restart_attempts = 0
            if restart_attempts >= SCOUT_MAX_AUTOMATIC_RESTARTS:
                fatal_code = "review_scout_restart_limit_exceeded"
                scout_debug_event(
                    "scout_fatal_stop",
                    scout_type="review",
                    error_code=fatal_code,
                    restart_attempts=restart_attempts,
                    last_error_type=exc.last_error_type,
                )
                await report_scout_fatal_status(
                    base_url=args.base_url,
                    agent_id=args.agent_id,
                    token=args.token,
                    machine_label=machine_label,
                    error_code=fatal_code,
                )
                raise ScoutFatalStop(
                    "Review Scout stopped after "
                    + str(SCOUT_MAX_AUTOMATIC_RESTARTS)
                    + " automatic restarts without healthy progress."
                ) from exc

            restart_attempts += 1
            delay = scout_restart_delay_seconds(restart_attempts)
            scout_debug_event(
                "scout_automatic_restart",
                scout_type="review",
                restart_attempt=restart_attempts,
                restart_limit=SCOUT_MAX_AUTOMATIC_RESTARTS,
                delay_seconds=delay,
                trigger=exc.error_code,
                last_error_type=exc.last_error_type,
            )
            print(
                "Restarting Review Scout "
                + str(restart_attempts)
                + "/"
                + str(SCOUT_MAX_AUTOMATIC_RESTARTS)
                + " in "
                + str(delay)
                + "s after repeated errors."
            )
            await asyncio.sleep(delay)


async def run(args: argparse.Namespace) -> None:
    profile_lock = ScoutProfileLock(args.profile_dir)
    profile_lock.acquire()
    try:
        if sys.platform == "win32":
            _recover_windows_scout_profile(args.profile_dir)
        if args.bootstrap_login:
            bootstrap_login(args.profile_dir, args.chrome_executable)
        elif args.agent_id:
            await supervise_review_scout(args)
        else:
            await run_legacy(args)
    finally:
        profile_lock.release()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except ScoutFatalStop as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(SCOUT_FATAL_EXIT_CODE)
