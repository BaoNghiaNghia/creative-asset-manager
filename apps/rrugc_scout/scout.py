from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit

import httpx


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
        """() => {
          const out = [];
          const anchors = Array.from(document.querySelectorAll('a[href*="/pin/"]'));
          for (const anchor of anchors) {
            const href = anchor.href;
            const images = Array.from(anchor.querySelectorAll('img'));
            for (const image of images) {
              const src = image.currentSrc || image.src || '';
              if (!src) continue;
              out.push({
                pin_url: href,
                image_url: src,
                alt_text: image.alt || image.getAttribute('aria-label') || anchor.getAttribute('aria-label') || null,
              });
            }
          }
          return out;
        }"""
    )
    return normalize_candidates(rows)


class CamClient:
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


async def scan_page(page: Any, client: CamClient, task: dict[str, Any]) -> None:
    query = quote_plus(str(task["query"]))
    url = "https://www.pinterest.com/search/pins/?q=" + query
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(2_500)

    if "/login" in page.url:
        await client.heartbeat("needs_login")
        print("Pinterest requires login. Sign in manually in the opened browser window.")
        await asyncio.to_thread(input, "After login is complete, press Enter here to continue: ")
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2_000)
        if "/login" in page.url:
            raise RuntimeError("Pinterest login is still required for this persistent profile.")

    seen: set[tuple[str, str]] = set()
    target = int(task["target_count"])
    max_scroll_batches = int(task["max_scroll_batches"])
    auto_import = bool(task["auto_import"])
    starting = int(task["drive_ready"] if auto_import else task["discovered"])

    for batch in range(max_scroll_batches):
        await client.heartbeat("busy")
        visible = await extract_visible(page)
        fresh = [
            row for row in visible
            if (row.pin_url, row.image_url) not in seen
        ]
        for row in fresh:
            seen.add((row.pin_url, row.image_url))

        submit_size = 1 if auto_import else 10
        for start in range(0, len(fresh), submit_size):
            chunk = fresh[start:start + submit_size]
            if not chunk:
                continue
            result = await client.submit(chunk)
            created = int(result.get("created") or 0)
            if auto_import:
                latest = await client.task()
                starting = int(latest["drive_ready"])
            else:
                starting += created
            print(
                "batch="
                + str(batch + 1)
                + "/"
                + str(max_scroll_batches)
                + " submitted="
                + str(len(chunk))
                + " created="
                + str(created)
                + " total="
                + str(starting)
            )
            if starting >= target:
                await client.heartbeat("ready")
                return

        await page.mouse.wheel(0, 2200)
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


async def run(args: argparse.Namespace) -> None:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realistic Review UGC Pinterest Browser Scout")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--chrome-executable", default="")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Not recommended for first login/session setup.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
