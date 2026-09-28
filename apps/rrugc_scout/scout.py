from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit

import httpx


CLIENT_VERSION = "rrugc-scout-v5"
IDLE_DIAGNOSTIC_INTERVAL_SECONDS = 30
INITIAL_RESULTS_TIMEOUT_MS = 6_000
SCROLL_RESULTS_TIMEOUT_MS = 3_500
MANUAL_GATE_POLL_MS = 1_500
QUALITY_FIRST_RUN_CANDIDATE_CAP = 24
QUALITY_QUERY_SUFFIX = "authentic candid lifestyle photo real people"
HEARTBEAT_INTERVAL_SECONDS = 10


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
        initial_dwell_ms=(4_500, 7_000),
        inspect_dwell_ms=(1_400, 2_600),
        submit_batch_size=3,
        submit_pause_ms=(1_500, 2_800),
        scroll_step_px=(420, 700),
        scroll_steps_per_batch=(3, 5),
        scroll_step_pause_ms=(650, 1_150),
        keyword_pause_ms=(3_500, 6_000),
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
    seen: set[tuple[str, str]] = set()
    result: list[Candidate] = []
    for row in rows:
        pin_url = str(row.get("pin_url") or "").strip()
        image_url = str(row.get("image_url") or "").strip()
        alt_text = str(row.get("alt_text") or "").strip() or None
        context_text = str(row.get("context_text") or "").strip()[:1200] or None
        if not allowed_pin(pin_url) or not allowed_image(image_url):
            continue
        key = (pin_url, image_url)
        if key in seen:
            continue
        seen.add(key)
        result.append(Candidate(pin_url, image_url, alt_text, context_text))
    return result


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
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/runs/{run_id}/candidates",
            json={"items": [row.as_json() for row in rows]},
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


async def scan_auto_run(
    page: Any,
    client: AutoScoutClient,
    task: dict[str, Any],
    *,
    login_wait_seconds: int,
    pace_name: str = "careful",
) -> None:
    pace = SCOUT_PACES.get(pace_name, SCOUT_PACES["careful"])
    run_id = str(task["run"]["id"])
    raw_queries = task.get("search_queries") or [task["query"]]
    search_queries: list[str] = []
    seen_queries: set[str] = set()
    for raw in raw_queries:
        value = str(raw or "").strip()
        key = value.casefold()
        if value and key not in seen_queries:
            seen_queries.add(key)
            search_queries.append(value)
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

    seen: set[tuple[str, str]] = set()
    target = int(task["target_count"])
    max_scroll_batches = int(task["max_scroll_batches"])
    progress = int(task.get("progress") or 0)
    pipeline_count = int(task.get("pipeline_count") or progress)
    remaining_pipeline_budget = max(0, target - pipeline_count)
    run_candidate_cap = min(
        QUALITY_FIRST_RUN_CANDIDATE_CAP,
        remaining_pipeline_budget,
    )
    submitted_this_run = 0
    if run_candidate_cap <= 0:
        await client.complete(run_id, "completed")
        return

    for query_index, raw_query in enumerate(search_queries):
        search_query = quality_search_query(raw_query)
        query = quote_plus(search_query)
        url = "https://www.pinterest.com/search/pins/?q=" + query
        await heartbeat_if_due(force=True)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        loaded_count = await wait_for_pin_growth(
            page,
            previous_count=0,
            timeout_ms=INITIAL_RESULTS_TIMEOUT_MS,
        )
        initial_dwell = await paced_wait(page, pace.initial_dwell_ms)

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
            unseen = [
                row
                for row in visible
                if (row.pin_url, row.image_url) not in seen
            ]
            for row in unseen:
                seen.add((row.pin_url, row.image_url))
            fresh, metadata_filtered = quality_prefilter(unseen)
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

            for start in range(0, len(fresh), pace.submit_batch_size):
                remaining_run_budget = run_candidate_cap - submitted_this_run
                if remaining_run_budget <= 0:
                    await client.complete(run_id, "completed")
                    return
                chunk = fresh[
                    start:start + min(pace.submit_batch_size, remaining_run_budget)
                ]
                if not chunk:
                    continue
                result = await client.submit(run_id, chunk)
                submitted_this_run += len(chunk)
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
                    + str(len(chunk))
                    + " created="
                    + str(result.get("created") or 0)
                    + " existing="
                    + str(result.get("existing") or 0)
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
                    or submitted_this_run >= run_candidate_cap
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


async def scan_page(page: Any, client: CamClient, task: dict[str, Any]) -> None:
    """Legacy bounded one-shot campaign scan."""
    query = quote_plus(str(task["query"]))
    url = "https://www.pinterest.com/search/pins/?q=" + query
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(2_500)

    gate = await access_gate(page)
    if gate is not None:
        await client.heartbeat("needs_login")
        raise RuntimeError(
            "Pinterest requires manual login/challenge resolution for this persistent profile. "
            "Use Auto Scout v5 for automatic resume after manual resolution."
        )

    seen: set[tuple[str, str]] = set()
    target = int(task["target_count"])
    max_scroll_batches = int(task["max_scroll_batches"])
    auto_import = bool(task["auto_import"])
    starting = int(task["drive_ready"] if auto_import else task["approved"])

    for batch in range(max_scroll_batches):
        await client.heartbeat("busy")
        visible = await extract_visible(page)
        fresh = [
            row for row in visible
            if (row.pin_url, row.image_url) not in seen
        ]
        for row in fresh:
            seen.add((row.pin_url, row.image_url))

        for start in range(0, len(fresh), 25):
            chunk = fresh[start:start + 25]
            if not chunk:
                continue
            result = await client.submit(chunk)
            latest = await client.task()
            starting = int(latest["drive_ready"] if auto_import else latest["approved"])
            print(
                "batch="
                + str(batch + 1)
                + "/"
                + str(max_scroll_batches)
                + " submitted="
                + str(len(chunk))
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

    playwright = await async_playwright().start()
    options: dict[str, Any] = {
        "user_data_dir": str(Path(args.profile_dir).expanduser().resolve()),
        "headless": args.headless,
        "viewport": {"width": 1440, "height": 1000},
    }
    chrome = resolve_chrome_executable(args.chrome_executable)
    if chrome:
        options["executable_path"] = chrome
        print("Using local Chrome: " + chrome)
    else:
        print("Local Chrome not found; using Playwright Chromium.")
    context = await playwright.chromium.launch_persistent_context(**options)
    return playwright, context


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
        await scan_page(page, client, task)
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
    idle_failures = 0
    last_idle_diagnostic_at = 0.0
    try:
        playwright, context = await launch_context(args)
        page = context.pages[0] if context.pages else await context.new_page()
        await client.heartbeat("ready")
        print(
            "Pinterest Auto Scout online. agent="
            + args.agent_id
            + " machine="
            + machine_label
            + " pace="
            + args.pace
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
                    )
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
    if args.bootstrap_login:
        bootstrap_login(args.profile_dir, args.chrome_executable)
    elif args.agent_id:
        await run_agent(args)
    else:
        await run_legacy(args)


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
