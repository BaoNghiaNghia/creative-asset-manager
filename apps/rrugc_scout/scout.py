from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit

import httpx


CLIENT_VERSION = "rrugc-scout-v17"
IDLE_DIAGNOSTIC_INTERVAL_SECONDS = 30
PIN_DETAIL_CONCURRENCY = 1
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
HEARTBEAT_INTERVAL_SECONDS = 10
SCOUT_HISTORY_FILENAME = "cam-pinterest-scout-history.json"
SCOUT_INSTANCE_LOCK_FILENAME = "cam-pinterest-scout-instance.json"
SCOUT_HISTORY_MAX_PINS_PER_CAMPAIGN = 50_000


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
        "$scoutMarker = (" + scout_marker + ").ToLowerInvariant(); "
        "$currentPid = " + str(os.getpid()) + "; "
        "Get-CimInstance Win32_Process | ForEach-Object { "
        "$name = [string]$_.Name; "
        "$cmd = [string]$_.CommandLine; "
        "$pidValue = [int]$_.ProcessId; "
        "if ($pidValue -ne $currentPid -and -not [string]::IsNullOrWhiteSpace($cmd)) { "
        "$lower = $cmd.ToLowerInvariant(); "
        "if ($name -ieq 'chrome.exe' -and $lower.Contains($needle)) { "
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
            "Replacing legacy/stale Pinterest Scout process(es) bound to this profile: "
            + ", ".join(str(pid) for pid in scout_pids)
        )
        _terminate_windows_process_trees(scout_pids)

    if chrome_pids:
        print(
            "Closing Chrome process tree(s) bound to the dedicated Pinterest Scout profile: "
            + ", ".join(str(pid) for pid in chrome_pids)
        )
        _terminate_windows_profile_chrome(chrome_pids)

    if scout_pids or chrome_pids:
        time.sleep(0.75)
        remaining = _windows_profile_owners(profile_dir)
        if remaining is not None and (remaining[0] or remaining[1]):
            parts: list[str] = []
            if remaining[1]:
                parts.append("Scout PID " + ", ".join(str(pid) for pid in remaining[1]))
            if remaining[0]:
                parts.append("Chrome PID " + ", ".join(str(pid) for pid in remaining[0]))
            raise RuntimeError(
                "The dedicated Pinterest Scout profile is still owned after cleanup ("
                + "; ".join(parts)
                + "). End only those Scout-profile processes in Task Manager and run "
                "START_SCOUT.bat again."
            )

    removed = _clear_stale_profile_runtime_files(profile_dir)
    if removed:
        print("Cleared stale Scout profile runtime files: " + ", ".join(removed))



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
        "Complete Pinterest sign-in manually. If you use Continue with Google, do it "
        "in this normal Chrome window. When Pinterest is fully signed in, close this "
        "Chrome window before starting Auto Scout."
    )
    process = subprocess.Popen([
        chrome,
        "--user-data-dir=" + profile,
        "--no-first-run",
        "https://www.pinterest.com/login/",
    ])
    process.wait()
    print("Bootstrap Chrome closed. The persistent Pinterest session is ready for Auto Scout.")


class PinterestAccessGateError(RuntimeError):
    def __init__(self, gate: str) -> None:
        self.gate = gate
        super().__init__("Pinterest access gate: " + gate)


class PinterestRateLimitedError(RuntimeError):
    pass


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
        self.data: dict[str, Any] = {"version": 1, "campaigns": {}}
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

    def remember(self, campaign_id: str, rows: list[Candidate]) -> int:
        if not rows:
            return 0
        campaigns = self.data.setdefault("campaigns", {})
        if not isinstance(campaigns, dict):
            campaigns = {}
            self.data["campaigns"] = campaigns
        key = str(campaign_id)
        campaign = campaigns.setdefault(key, {"seen_pins": []})
        if not isinstance(campaign, dict):
            campaign = {"seen_pins": []}
            campaigns[key] = campaign
        pins = campaign.get("seen_pins")
        if not isinstance(pins, list):
            pins = []
        ordered = [
            pin_history_key(value)
            for value in pins
            if isinstance(value, str) and value.strip()
        ]
        known = set(ordered)
        added = 0
        for row in rows:
            pin = pin_history_key(row.pin_url)
            if not pin or pin in known:
                continue
            known.add(pin)
            ordered.append(pin)
            added += 1
        campaign["seen_pins"] = ordered[-self.max_pins_per_campaign:]
        if added:
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
    lowered = clean.casefold()
    if any(
        marker in lowered
        for marker in ("photo", "photography", "candid", "lifestyle")
    ):
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


async def extract_visible(page: Any) -> list[Candidate]:
    rows = await page.evaluate(
        r"""() => {
          const out = [];
          const anchors = Array.from(document.querySelectorAll('a[href*="/pin/"]'));
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
          for (const anchor of anchors) {
            const href = anchor.href;
            const images = Array.from(anchor.querySelectorAll('img'));
            const card = anchor.closest('[data-grid-item="true"]')
              || anchor.parentElement?.parentElement
              || anchor.parentElement;
            const contextText = (card?.textContent || '').trim().slice(0, 1200);
            for (const image of images) {
              const src = bestSrc(image);
              if (!src) continue;
              out.push({
                pin_url: href,
                image_url: src,
                alt_text: image.alt
                  || image.getAttribute('aria-label')
                  || anchor.getAttribute('aria-label')
                  || null,
                context_text: contextText || null,
              });
            }
          }
          return out;
        }"""
    )
    return normalize_candidates(rows)


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
                1,
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


async def resolve_pin_details(
    search_page: Any,
    rows: list[Candidate],
    *,
    detail_page: Any | None = None,
    concurrency: int = PIN_DETAIL_CONCURRENCY,
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
                resolved.append(
                    await extract_pin_detail_candidate(detail_page, seed)
                )
            except (PinterestAccessGateError, PinterestRateLimitedError):
                raise
            except Exception as exc:
                print(
                    "Pinterest Pin detail fallback: "
                    + seed.pin_url
                    + " ("
                    + exc.__class__.__name__
                    + ")"
                )
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
    return "challenge" if detected else None


async def loaded_pin_count(page: Any) -> int:
    value = await page.evaluate(
        r"""() => document.querySelectorAll('a[href*="/pin/"] img').length"""
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

    async def heartbeat(
        self,
        status: str,
        *,
        run_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/heartbeat",
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
        response = await self.client.post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/claim",
            headers={
                "X-Scout-Version": CLIENT_VERSION,
                "X-Scout-Machine": self.machine_label,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload or None

    async def diagnostics(self) -> dict[str, Any]:
        response = await self.client.get(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/diagnostics"
        )
        response.raise_for_status()
        return response.json()

    async def submit(
        self,
        run_id: str,
        rows: list[Candidate],
        *,
        source_query: str | None = None,
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/runs/{run_id}/candidates",
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
        response = await self.client.post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/runs/{run_id}/complete",
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
            for level in ("direct", "text_match", "adjacent"):
                values = clusters.get(level)
                if isinstance(values, list):
                    raw_queries.extend(values)

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
    history_key = source_plan_id or campaign_id
    persistent_seen = history.seen_pin_keys(history_key) if history else set()
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

        for batch in range(max_scroll_batches):
            await heartbeat_if_due()
            inspect_dwell = await paced_wait(page, pace.inspect_dwell_ms)
            visible = await extract_visible(page)
            visible_once = [
                row
                for row in visible
                if pin_history_key(row.pin_url) not in seen_pins
            ]
            persistent_skipped = sum(
                1
                for row in visible_once
                if pin_history_key(row.pin_url) in persistent_seen
            )
            unseen = [
                row
                for row in visible_once
                if pin_history_key(row.pin_url) not in persistent_seen
            ]
            for row in visible_once:
                seen_pins.add(pin_history_key(row.pin_url))
            filtered_rows = [
                row for row in unseen
                if synthetic_metadata_reason(row) is not None
            ]
            fresh, metadata_filtered = quality_prefilter(unseen)
            if history and filtered_rows:
                history.remember(history_key, filtered_rows)
                persistent_seen.update(
                    pin_history_key(row.pin_url) for row in filtered_rows
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
                upgraded = sum(
                    1
                    for before, after in zip(chunk, resolved_chunk)
                    if before.image_url != after.image_url
                )
                if upgraded:
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
                if history:
                    history.remember(history_key, resolved_chunk)
                    persistent_seen.update(
                        pin_history_key(row.pin_url) for row in resolved_chunk
                    )
                created_now = int(result.get("created") or 0)
                created_this_run += created_now
                created_for_keyword += created_now
                progress = int(result.get("progress") or 0)
                pipeline_count = int(result.get("pipeline_count") or progress)
                print(
                    "campaign="
                    + str(task["campaign_id"])
                    + " keyword="
                    + str(query_index + 1)
                    + "/"
                    + str(len(search_queries))
                    + " batch="
                    + str(batch + 1)
                    + "/"
                    + str(max_scroll_batches)
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

            if batch + 1 < max_scroll_batches:
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
    return message


async def run_agent(args: argparse.Namespace) -> None:
    machine_label = args.machine_label or socket.gethostname()
    client = AutoScoutClient(
        args.base_url,
        args.agent_id,
        args.token,
        machine_label=machine_label,
    )
    playwright = None
    context = None
    history = ScoutHistory(
        Path(args.profile_dir).expanduser().resolve() / SCOUT_HISTORY_FILENAME
    )
    idle_failures = 0
    last_idle_diagnostic_at = 0.0
    try:
        playwright, context = await launch_context(args)
        page = context.pages[0] if context.pages else await context.new_page()
        for stale_page in list(context.pages[1:]):
            try:
                await stale_page.close()
            except Exception:
                pass
        detail_page = await context.new_page()
        await client.heartbeat("ready")
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
                            print(
                                "Scout idle: no campaign claimed; diagnostics unavailable: "
                                + exc.__class__.__name__
                            )
                        last_idle_diagnostic_at = now
                    await asyncio.sleep(args.poll_interval_seconds)
                    continue
                try:
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
                await asyncio.sleep(delay)
            except Exception as exc:
                idle_failures += 1
                try:
                    await client.heartbeat(
                        "error",
                        error_code="scout_runtime_error",
                    )
                except Exception:
                    pass
                delay = min(60, max(5, 2 ** min(idle_failures, 6)))
                print(
                    "Scout run failed; keeping the persistent browser alive and retrying in "
                    + str(delay)
                    + "s: "
                    + exc.__class__.__name__
                )
                if args.once:
                    raise
                await asyncio.sleep(delay)
    finally:
        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()
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


async def run(args: argparse.Namespace) -> None:
    profile_lock = ScoutProfileLock(args.profile_dir)
    profile_lock.acquire()
    try:
        if sys.platform == "win32":
            _recover_windows_scout_profile(args.profile_dir)
        if args.bootstrap_login:
            bootstrap_login(args.profile_dir, args.chrome_executable)
        elif args.agent_id:
            await run_agent(args)
        else:
            await run_legacy(args)
    finally:
        profile_lock.release()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
