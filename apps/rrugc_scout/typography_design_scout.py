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
    counts = {
        "keywords": 0, "picked_source_candidates": 0,
        "new_search_candidates": 0, "created": 0, "duplicate": 0, "failed": 0,
    }
    attempts = 0
    seen: set[str] = set()
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        headers={"Authorization": "Bearer " + token},
        timeout=httpx.Timeout(25.0, connect=8.0),
        follow_redirects=False,
    ) as api:
        reply = await api.get(endpoint + "/keywords", params={"limit": args.keyword_limit})
        reply.raise_for_status()
        discovery = reply.json()
        selected = discovery.get("keywords", [])
        if not selected:
            return counts
        # Backwards compatible: older CAM versions return only "keywords".
        # Newer APIs rotate the independent Stage 0 seed lane past imported Pins.
        pending_sources = discovery.get("pending_sources", selected)
        counts["keywords"] = len(selected)
        counts["seed_keywords"] = len(pending_sources)
        if not args.execute:
            for row in selected:
                print("DRY RUN picked keyword: " + str(row["keyword"])[:80])
            return counts

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=7.0),
            follow_redirects=False,
        ) as downloads:

            async def collect(
                *, pin_url: str, image_url: str,
                keyword_id: str, source_query: str, lane: str,
            ) -> bool:
                """Ingest one Pinterest image as unknown-rights candidate, never approve."""
                nonlocal attempts
                identity = pin_id(pin_url)
                if not identity or identity in seen or not allowed_image(image_url):
                    return False
                if attempts >= 24 or counts["created"] + counts["duplicate"] >= args.max_images:
                    return False
                attempts += 1
                seen.add(identity)
                fetched = await download_image(downloads, image_url)
                if fetched is None:
                    counts["failed"] += 1
                    return False
                image_data, mime = fetched
                try:
                    upload = await api.post(
                        endpoint + "/references",
                        data={
                            "source_url": pin_url,
                            "source_query": source_query[:120],
                            "keyword_id": keyword_id,
                        },
                        files={"file": (
                            "typography-pin-" + identity + ACCEPTED_MIME[mime],
                            image_data, mime,
                        )},
                    )
                    if upload.status_code == 429:
                        print("Daily Design Scout intake limit reached.")
                        return True
                    upload.raise_for_status()
                    state = str(upload.json().get("status") or "")
                    if state not in {"created", "duplicate"}:
                        counts["failed"] += 1
                        return False
                    counts[state] += 1
                    counts[lane] += 1
                except (httpx.HTTPError, ValueError):
                    counts["failed"] += 1
                return False

            # Reuse real Pinterest results already found by Stage 0 Scout first.
            # No Chrome login or new crawl is needed to collect these candidates.
            # Original source rights remain unknown, not licensed/approved.
            for row in pending_sources:
                if attempts >= 24 or counts["created"] + counts["duplicate"] >= args.max_images:
                    return counts
                if not row.get("source_pin_url") or not row.get("source_image_url"):
                    continue
                hit_limit = await collect(
                    pin_url=str(row["source_pin_url"]),
                    image_url=str(row["source_image_url"]),
                    keyword_id=str(row["id"]),
                    source_query="Picked Stage 0 Pinterest reference",
                    lane="picked_source_candidates",
                )
                if hit_limit:
                    return counts

            if args.seed_only or counts["created"] + counts["duplicate"] >= args.max_images:
                return counts

            # Independent fresh Pinterest search uses only its own isolated
            # profile, never another Scout's session or user browser cookies.
            lock = ScoutProfileLock(args.profile_dir)
            lock.acquire()
            playwright = context = None
            try:
                try:
                    playwright, context = await launch_context(SimpleNamespace(
                        profile_dir=args.profile_dir, headless=args.headless,
                        chrome_executable=args.chrome_executable,
                    ))
                except Exception:
                    # A dedicated Chrome profile may not launch in a noninteractive
                    # Windows session or when Chromium/Chrome conflicts with
                    # existing profile ownership. Keep Stage 0 candidates and
                    # never turn a successful import into a failed whole run.
                    # Do not print raw Playwright stderr (may contain local paths).
                    print("LIVE_PINTEREST_BROWSER_UNAVAILABLE: isolated Chrome failed "
                          "to start. Stage 0 candidate import is preserved. "
                          "Close only the Typography Chrome window and try fresh "
                          "Pinterest discovery after manual sign-in.")
                    counts["browser_status"] = "unavailable"
                    return counts
                page = context.pages[0] if context.pages else await context.new_page()
                for row in selected:
                    for query in search_queries(str(row["keyword"])):
                        try:
                            pins = await scan_one_query(page, query=query)
                        except RuntimeError as exc:
                            if ("Pinterest login or verification required" in str(exc)
                                    or str(exc).startswith("Pinterest access gate:")):
                                print("LIVE_PINTEREST_LOGIN_REQUIRED: log into the isolated "
                                      "profile with START_SCOUT_TYPOGRAPHY_LOGIN.cmd. "
                                      "Existing Stage 0 source candidates were kept.")
                                if counts["created"] + counts["duplicate"] > 0:
                                    return counts
                            raise
                        for pin in pins:
                            hit_limit = await collect(
                                pin_url=pin.pin_url, image_url=pin.image_url,
                                keyword_id=str(row["id"]),
                                source_query=query, lane="new_search_candidates",
                            )
                            if hit_limit:
                                return counts
                            if attempts >= 24 or counts["created"] + counts["duplicate"] >= args.max_images:
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
    parser.add_argument("--seed-only", action="store_true",
                        help="Import already discovered picked Stage 0 Pinterest sources, no Chrome.")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if not (1 <= args.keyword_limit <= 6 and 1 <= args.max_images <= MAX_IMAGES_PER_RUN):
        parser.error("Use 1-6 picked keywords and at most 8 images per run.")
    try:
        result = asyncio.run(scout(args))
    except RuntimeError as exc:
        # No source URLs or browser internal errors in logs. Give an actionable
        # explanation for the new, isolated Chrome profile sign-in case.
        if ("Pinterest login or verification required" in str(exc)
                or str(exc).startswith("Pinterest access gate:")):
            print("PINTEREST_LOGIN_REQUIRED: run START_SCOUT_TYPOGRAPHY_LOGIN.cmd once, "
                  "sign in to Pinterest, close that Chrome window, then rerun Scout.")
            return 21
        print("Design Scout browser failure: " + type(exc).__name__)
        return 1
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            print("TYPOGRAPHY_SCOUT_PAIRING_REQUIRED: check the paired CAM Scout agent.")
        elif code == 404:
            print("TYPOGRAPHY_SCOUT_API_UNAVAILABLE: verify the CAM Typography pilot flag and API.")
        else:
            print("TYPOGRAPHY_SCOUT_API_ERROR: HTTP " + str(code))
        return 1
    except (httpx.HTTPError, OSError) as exc:
        print("TYPOGRAPHY_SCOUT_NETWORK_ERROR: " + type(exc).__name__)
        return 1
    print("Typography Design Scout: " + str(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
