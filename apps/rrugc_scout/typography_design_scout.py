"""One-shot Pinterest Typography Design Scout for CAM, using the existing Scout login.

Find a few keyword-relevant design references, upload them as unapproved
Typography Ideas candidates and stop. Never marks image rights approved,
generates AI art, edits Stage 1 V1, or starts a continuous task.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote_plus, urlsplit

import httpx

from scout import (
    ScoutProfileLock, access_gate, allowed_image, extract_visible,
    guard_pinterest_response, launch_context,
)

BASE_PATH = "/api/v1/realistic-review-ugc/typography/scout-agents/"
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGES_PER_RUN = 8
PIN_RE = re.compile(r"^/pin/([0-9]{5,25})/?$")
ACCEPTED_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def pin_id(source_url: str) -> str | None:
    try:
        address = urlsplit(source_url.strip())
        if (address.scheme != "https" or address.hostname not in
                ("pinterest.com", "www.pinterest.com")):
            return None
        match = PIN_RE.fullmatch(address.path)
        return match.group(1) if match else None
    except ValueError:
        return None


def search_queries(keyword: str) -> tuple[str, ...]:
    value = " ".join(keyword.split())[:80]
    if not value:
        return ()
    return (value + " embroidered hat typography design",
            value + " retro vintage lettering trucker cap")


async def download_image(client: httpx.AsyncClient, image_url: str) -> tuple[bytes, str] | None:
    if not allowed_image(image_url):
        return None
    try:
        async with client.stream("GET", image_url) as reply:
            if reply.status_code != 200:
                return None
            mime = reply.headers.get("content-type", "").split(";")[0].lower().strip()
            if mime not in ACCEPTED_MIME:
                return None
            if int(reply.headers.get("content-length") or 0) > MAX_DOWNLOAD_BYTES:
                return None
            parts = bytearray()
            async for chunk in reply.aiter_bytes():
                if len(parts) + len(chunk) > MAX_DOWNLOAD_BYTES:
                    return None
                parts.extend(chunk)
            return (bytes(parts), mime) if parts else None
    except (httpx.HTTPError, ValueError):
        return None


async def scan_one_query(page, *, query: str) -> list:
    url = "https://www.pinterest.com/search/pins/?q=" + quote_plus(query)
    response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    guard_pinterest_response(response)
    await page.wait_for_timeout(1800)
    gate = await access_gate(page)
    if gate is not None:
        raise RuntimeError("Pinterest login or verification required; no upload attempted.")
    candidates: dict[str, object] = {}
    for pass_index in range(3):
        for row in await extract_visible(page):
            identity = pin_id(row.pin_url)
            if identity and identity not in candidates and allowed_image(row.image_url):
                candidates[identity] = row
            if len(candidates) >= 18:
                break
        if pass_index < 2 and len(candidates) < 18:
            await page.mouse.wheel(0, 1150)
            await page.wait_for_timeout(1200)
    return list(candidates.values())[:18]


async def scout(args: argparse.Namespace) -> dict:
    token = (os.getenv("RRUGC_SCOUT_TOKEN") or "").strip()
    if not token or not args.agent_id:
        raise RuntimeError("Paired Scout Agent ID/token missing in local configuration.")
    if not args.base_url.startswith("https://"):
        raise RuntimeError("CAM endpoint must use HTTPS.")
    endpoint = BASE_PATH + args.agent_id
    counts = {"keywords": 0, "found": 0, "created": 0, "duplicate": 0, "failed": 0}
    attempts = 0
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        headers={"Authorization": "Bearer " + token},
        timeout=httpx.Timeout(25.0, connect=8.0),
        follow_redirects=False,
    ) as api:
        reply = await api.get(endpoint + "/keywords", params={"limit": args.keyword_limit})
        reply.raise_for_status()
        selected = reply.json().get("keywords", [])
        if not selected:
            return counts
        counts["keywords"] = len(selected)
        # GET the server-side picked keyword list BEFORE opening Pinterest.
        if not args.execute:
            for row in selected:
                print("DRY RUN picked keyword: " + str(row["keyword"])[:80])
            return counts
        lock = ScoutProfileLock(args.profile_dir)
        lock.acquire()
        playwright = context = None
        try:
            playwright, context = await launch_context(SimpleNamespace(
                profile_dir=args.profile_dir, headless=args.headless,
                chrome_executable=args.chrome_executable,
            ))
            page = context.pages[0] if context.pages else await context.new_page()
            seen: set[str] = set()
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=7.0),
                follow_redirects=False,
            ) as downloads:
                for selected_row in selected:
                    for query in search_queries(str(selected_row["keyword"])):
                        pins = await scan_one_query(page, query=query)
                        counts["found"] += len(pins)
                        for pin in pins:
                            identity = pin_id(pin.pin_url)
                            if not identity or identity in seen:
                                continue
                            seen.add(identity)
                            attempts += 1
                            if attempts > 24:  # hard cap even when every image/download fails
                                return counts
                            fetched = await download_image(downloads, pin.image_url)
                            if fetched is None:
                                continue
                            image_data, mime = fetched
                            try:
                                upload = await api.post(
                                    endpoint + "/references",
                                    data={
                                        "source_url": pin.pin_url,
                                        "source_query": query,
                                        "keyword_id": str(selected_row["id"]),
                                    },
                                    files={"file": ("typography-pin-" + identity +
                                                    ACCEPTED_MIME[mime], image_data, mime)},
                                )
                                if upload.status_code == 429:
                                    print("Daily Design Scout intake limit reached.")
                                    return counts
                                upload.raise_for_status()
                                result = upload.json()
                                state = str(result.get("status") or "")
                                counts[state] = counts.get(state, 0) + 1
                            except (httpx.HTTPError, ValueError):
                                counts["failed"] += 1
                            if counts["created"] + counts["duplicate"] >= args.max_images:
                                return counts
                        if counts["created"] + counts["duplicate"] >= args.max_images:
                            return counts
            return counts
        finally:
            if context is not None:
                await context.close()
            if playwright is not None:
                await playwright.stop()
            lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--chrome-executable", default="")
    parser.add_argument("--keyword-limit", type=int, default=3)
    parser.add_argument("--max-images", type=int, default=MAX_IMAGES_PER_RUN)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if not (1 <= args.keyword_limit <= 6 and 1 <= args.max_images <= MAX_IMAGES_PER_RUN):
        parser.error("Use 1-6 picked keywords and at most 8 images per run.")
    try:
        result = asyncio.run(scout(args))
    except (httpx.HTTPError, RuntimeError, OSError) as exc:
        # Do not reveal bearer credentials, source URLs, or raw response payload.
        print("Design Scout failed safely: " + type(exc).__name__)
        return 1
    print("Typography Design Scout: " + str(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
