from __future__ import annotations

import argparse
import asyncio
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit

import httpx


CLIENT_VERSION = "rrugc-scout-v2"


@dataclass(frozen=True, slots=True)
class Candidate:
    pin_url: str
    image_url: str
    alt_text: str | None = None

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


def normalize_candidates(rows: list[dict[str, Any]]) -> list[Candidate]:
    seen: set[tuple[str, str]] = set()
    result: list[Candidate] = []
    for row in rows:
        pin_url = str(row.get("pin_url") or "").strip()
        image_url = str(row.get("image_url") or "").strip()
        alt_text = str(row.get("alt_text") or "").strip() or None
        if not allowed_pin(pin_url) or not allowed_image(image_url):
            continue
        key = (pin_url, image_url)
        if key in seen:
            continue
        seen.add(key)
        result.append(Candidate(pin_url, image_url, alt_text))
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
        """() => Boolean(
          document.querySelector('iframe[src*="captcha" i], [data-test-id*="captcha" i], [id*="captcha" i]')
        )"""
    )
    return "challenge" if detected else None


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
        await page.wait_for_timeout(5_000)
        gate = await access_gate(page)
        if gate is None:
            await client.heartbeat("busy", run_id=run_id)
            return True
    return False


async def scan_auto_run(
    page: Any,
    client: AutoScoutClient,
    task: dict[str, Any],
    *,
    login_wait_seconds: int,
) -> None:
    run_id = str(task["run"]["id"])
    query = quote_plus(str(task["query"]))
    url = "https://www.pinterest.com/search/pins/?q=" + query
    await client.heartbeat("busy", run_id=run_id)
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(2_500)

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

    seen: set[tuple[str, str]] = set()
    target = int(task["target_count"])
    max_scroll_batches = int(task["max_scroll_batches"])
    progress = int(task.get("progress") or 0)

    for batch in range(max_scroll_batches):
        await client.heartbeat("busy", run_id=run_id)
        visible = await extract_visible(page)
        fresh = [
            row
            for row in visible
            if (row.pin_url, row.image_url) not in seen
        ]
        for row in fresh:
            seen.add((row.pin_url, row.image_url))

        for start in range(0, len(fresh), 25):
            chunk = fresh[start:start + 25]
            if not chunk:
                continue
            result = await client.submit(run_id, chunk)
            progress = int(result.get("progress") or 0)
            print(
                "campaign="
                + str(task["campaign_id"])
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
            )
            if result.get("campaign_status") != "running" or progress >= target:
                await client.complete(run_id, "completed")
                return

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

        await page.mouse.wheel(0, 2600)
        await page.wait_for_timeout(1_500)

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
            "Use Auto Scout v2 for automatic resume after manual resolution."
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
    if args.chrome_executable:
        options["executable_path"] = args.chrome_executable
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
    try:
        playwright, context = await launch_context(args)
        page = context.pages[0] if context.pages else await context.new_page()
        await client.heartbeat("ready")
        print(
            "Pinterest Auto Scout online. agent="
            + args.agent_id
            + " machine="
            + machine_label
        )
        while True:
            try:
                task = await client.claim()
                idle_failures = 0
                if task is None:
                    if args.once:
                        return
                    await asyncio.sleep(args.poll_interval_seconds)
                    continue
                try:
                    await scan_auto_run(
                        page,
                        client,
                        task,
                        login_wait_seconds=args.login_wait_seconds,
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
    parser.add_argument("--base-url", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--agent-id",
        help="Auto Scout v2 agent ID. Runs continuously and claims campaigns automatically.",
    )
    mode.add_argument(
        "--campaign-id",
        help="Legacy one-campaign mode.",
    )
    parser.add_argument("--token", default="")
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--chrome-executable", default="")
    parser.add_argument("--machine-label", default="")
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
    args.token = resolve_token(args)
    return args


async def run(args: argparse.Namespace) -> None:
    if args.agent_id:
        await run_agent(args)
    else:
        await run_legacy(args)


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
