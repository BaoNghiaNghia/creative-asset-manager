from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import random
import re
import sys
import time
from typing import Any
from urllib.parse import quote_plus

import httpx

from scout import (
    PinterestAccessGateError,
    PinterestRateLimitedError,
    SCOUT_PACES,
    access_gate,
    bootstrap_login,
    configure_scout_debug_log,
    extract_visible,
    guard_pinterest_response,
    launch_context,
    paced_wait,
    pin_history_key,
    pinimg_asset_key,
    resolve_pin_details,
    scout_debug_event,
    wait_for_pin_growth,
)


DEFAULT_BASE_URL = "https://creative-assets.ddns.net"
DEFAULT_PINTEREST_QUERY = "Saying Trucker hat"
DEFAULT_HISTORY_FILENAME = "keyword-scout-history.json"
DEFAULT_CYCLE_SECONDS = 180
DEFAULT_MAX_SCROLL_BATCHES = 10
DEFAULT_MAX_PINS_PER_CYCLE = 40
INITIAL_RESULTS_TIMEOUT_MS = 12_000
EMPTY_BATCH_RELOAD_THRESHOLD = 3
EMPTY_CYCLE_RETRY_SECONDS = 30
RUNTIME_RECOVERY_SECONDS = 30


def _keywords_from_file(path: str | None) -> list[str]:
    if not path:
        return []
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("Keyword JSON file must contain an array.")
        return [str(value) for value in parsed]
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _clean_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n\"'“”")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_keyword(value)
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _quote_extract_status_is_terminal(status: int) -> bool:
    return int(status) in {400, 413, 422}


def resolve_keyword_volume(
    *,
    base_url: str,
    agent_id: str,
    token: str,
    keywords: list[str],
    force: bool = False,
) -> dict:
    endpoint = (
        base_url.rstrip("/")
        + "/api/v1/realistic-review-ugc/scout-agents/"
        + agent_id
        + "/keyword-analysis/resolve"
    )
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=8.0)) as client:
        response = client.post(
            endpoint,
            headers={"Authorization": "Bearer " + token},
            json={
                "keywords": keywords,
                "force": force,
            },
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Creative Asset Manager returned an invalid response.")
    return payload


class KeywordScoutHistory:
    """Durable no-repeat history isolated from the Review Scout history."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "version": 1,
            "seen_pins": [],
            "seen_assets": [],
            "seen_quotes": [],
            "pending_quotes": [],
        }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self.data.update(payload)
        except (FileNotFoundError, OSError, ValueError, TypeError):
            pass

    def _set(self, name: str) -> set[str]:
        values = self.data.get(name)
        if not isinstance(values, list):
            return set()
        return {
            str(value).strip()
            for value in values
            if isinstance(value, str) and value.strip()
        }

    @property
    def seen_pins(self) -> set[str]:
        return self._set("seen_pins")

    @property
    def seen_assets(self) -> set[str]:
        return self._set("seen_assets")

    @property
    def seen_quotes(self) -> set[str]:
        return {value.casefold() for value in self._set("seen_quotes")}

    @property
    def pending_quotes(self) -> list[str]:
        values = self.data.get("pending_quotes")
        if not isinstance(values, list):
            return []
        return _dedupe([str(value) for value in values if str(value).strip()])

    @property
    def known_quote_keys(self) -> set[str]:
        return self.seen_quotes | {
            value.casefold()
            for value in self.pending_quotes
        }

    def remember_candidate(self, pin_url: str, image_url: str) -> None:
        pins = list(self._set("seen_pins"))
        assets = list(self._set("seen_assets"))
        pin = pin_history_key(pin_url)
        asset = pinimg_asset_key(image_url)
        if pin and pin not in pins:
            pins.append(pin)
        if asset and asset not in assets:
            assets.append(asset)
        self.data["seen_pins"] = pins[-50_000:]
        self.data["seen_assets"] = assets[-50_000:]
        self.save()

    def remember_quotes(self, quotes: list[str]) -> None:
        existing = list(self._set("seen_quotes"))
        known = {value.casefold() for value in existing}
        completed = {value.casefold() for value in _dedupe(quotes)}
        for quote in _dedupe(quotes):
            if quote.casefold() not in known:
                known.add(quote.casefold())
                existing.append(quote)
        self.data["seen_quotes"] = existing[-20_000:]
        self.data["pending_quotes"] = [
            value
            for value in self.pending_quotes
            if value.casefold() not in completed
        ]
        self.save()

    def remember_pending_quotes(self, quotes: list[str]) -> None:
        pending = self.pending_quotes
        known = {value.casefold() for value in pending} | self.seen_quotes
        for quote in _dedupe(quotes):
            if quote.casefold() not in known:
                known.add(quote.casefold())
                pending.append(quote)
        self.data["pending_quotes"] = pending[-20_000:]
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class QuoteScoutClient:
    def __init__(self, base_url: str, agent_id: str, token: str) -> None:
        self.agent_id = agent_id
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(45.0, connect=10.0),
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        operation: str,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            started = time.monotonic()
            try:
                response = await self.client.post(path, json=payload)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"CAM returned HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise RuntimeError("CAM returned an invalid JSON response.")
                scout_debug_event(
                    "keyword_scout_cam_request",
                    operation=operation,
                    status_code=response.status_code,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    attempt=attempt + 1,
                )
                return body
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
                status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    and exc.response is not None
                    else None
                )
                scout_debug_event(
                    "keyword_scout_cam_request_failed",
                    operation=operation,
                    status_code=status,
                    error_type=exc.__class__.__name__,
                    attempt=attempt + 1,
                )
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response is not None
                    and exc.response.status_code not in {429}
                    and exc.response.status_code < 500
                ):
                    raise
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt) + random.random())
        raise RuntimeError(f"{operation} failed after retries") from last_error

    async def extract_quote(self, candidate) -> dict[str, Any]:
        return await self._post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/quote-analysis/extract",
            {
                "pin_url": candidate.pin_url,
                "image_url": candidate.image_url,
                "alt_text": candidate.alt_text,
            },
            operation="extract_hat_quote",
        )

    async def resolve_volume(self, quotes: list[str]) -> dict[str, Any]:
        return await self._post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/keyword-analysis/resolve",
            {"keywords": quotes, "force": False},
            operation="resolve_keyword_volume",
        )


async def _wait_for_pinterest_access(page: Any, *, max_seconds: int = 900) -> None:
    gate = await access_gate(page)
    if gate is None:
        return
    if gate == "login":
        # Google commonly rejects sign-in from a Playwright-controlled browser.
        # Signal the caller to close automation and reopen this profile in
        # ordinary Chrome via bootstrap_login().
        raise PinterestAccessGateError("login")

    print(
        "Pinterest challenge detected for the Keyword Scout profile. "
        "Resolve it in the open Chrome window; this terminal will resume automatically."
    )
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        await page.wait_for_timeout(5_000)
        gate = await access_gate(page)
        if gate is None:
            print("Pinterest access restored. Keyword Scout is continuing.")
            return
        if gate == "login":
            raise PinterestAccessGateError("login")
    raise PinterestAccessGateError(gate or "challenge")


async def _scroll_search_page(page: Any, pace) -> None:
    steps = random.randint(*pace.scroll_steps_per_batch)
    for _ in range(steps):
        await page.mouse.wheel(0, random.randint(*pace.scroll_step_px))
        await page.wait_for_timeout(random.randint(*pace.scroll_step_pause_ms))


async def _search_page_diagnostics(page: Any) -> dict[str, Any]:
    payload = await page.evaluate(
        r"""() => ({
          url: window.location.href,
          title: document.title,
          ready_state: document.readyState,
          pin_links: new Set(
            Array.from(document.querySelectorAll('a[href*="/pin/"]'))
              .map((node) => node.href || node.getAttribute('href') || '')
              .filter(Boolean)
          ).size,
          pinimg_images: document.querySelectorAll(
            'img[src*="pinimg.com"], img[srcset*="pinimg.com"]'
          ).length,
          all_images: document.images.length,
          body_text: (document.body?.innerText || '')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 500),
        })"""
    )
    return payload if isinstance(payload, dict) else {}


async def _flush_pending_quote_volumes(
    client: QuoteScoutClient,
    history: KeywordScoutHistory,
) -> int:
    pending = history.pending_quotes
    if not pending:
        return 0

    saved = 0
    for start in range(0, len(pending), 50):
        chunk = pending[start:start + 50]
        try:
            result = await client.resolve_volume(chunk)
        except Exception as exc:
            scout_debug_event(
                "keyword_scout_pending_volume_failed",
                pending=len(chunk),
                error_type=exc.__class__.__name__,
            )
            print(
                "Pending keyword-volume retry failed; keeping "
                + str(len(chunk))
                + " quote(s) for the next cycle."
            )
            break
        history.remember_quotes(chunk)
        saved += len(chunk)
        scout_debug_event(
            "keyword_scout_pending_volume_saved",
            count=len(chunk),
            provider_requested=int(result.get("provider_requested") or 0),
            cached=int(result.get("cached") or 0),
        )
        print("Recovered pending keyword volumes: " + str(len(chunk)))
    return saved


async def run_pinterest_quote_scout(args: argparse.Namespace) -> None:
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    log_path = configure_scout_debug_log(
        profile_dir,
        filename="keyword-scout.jsonl",
        scout_type="keyword",
    )
    history = KeywordScoutHistory(profile_dir / DEFAULT_HISTORY_FILENAME)
    pace = SCOUT_PACES.get(args.pace, SCOUT_PACES["careful"])
    client = QuoteScoutClient(args.base_url, args.agent_id, args.token)

    print("Stage 0 Keyword Scout log: " + str(log_path))
    print("Pinterest query       : " + args.seed_query)
    print("Pinterest profile     : " + str(profile_dir))
    print("Mode                  : autonomous quote extraction + Google Ads volume")
    print("Loop                  : continuous until this terminal is closed")
    print("Review Scout state    : separate profile + separate history")
    scout_debug_event(
        "keyword_scout_started",
        query=args.seed_query,
        profile_dir=str(profile_dir),
        pace=pace.name,
        cycle_seconds=args.cycle_seconds,
        max_scroll_batches=args.max_scroll_batches,
        max_pins_per_cycle=args.max_pins_per_cycle,
        history_path=str(history.path),
    )

    playwright = None
    context = None
    page = None
    detail_page = None

    async def open_browser_runtime() -> None:
        nonlocal playwright, context, page, detail_page
        playwright, context = await launch_context(args)
        page = context.pages[0] if context.pages else await context.new_page()
        for stale_page in list(context.pages[1:]):
            try:
                await stale_page.close()
            except Exception:
                pass
        detail_page = await context.new_page()

    async def close_browser_runtime() -> None:
        nonlocal playwright, context, page, detail_page
        current_detail = detail_page
        current_context = context
        current_playwright = playwright
        detail_page = None
        page = None
        context = None
        playwright = None

        if current_detail is not None:
            try:
                await current_detail.close()
            except Exception:
                pass
        if current_context is not None:
            try:
                await current_context.close()
            except Exception:
                pass
        if current_playwright is not None:
            try:
                await current_playwright.stop()
            except Exception:
                pass

    async def bootstrap_keyword_login() -> None:
        await close_browser_runtime()
        print("")
        print(
            "Pinterest sign-in requires normal Chrome. "
            "Opening the dedicated Keyword Scout profile outside Playwright..."
        )
        print(
            "Complete Pinterest sign-in there. Continue with Google is supported "
            "in this normal Chrome window. Close the Chrome window after Pinterest "
            "is fully signed in; Keyword Scout will resume automatically."
        )
        scout_debug_event(
            "keyword_scout_login_bootstrap_started",
            profile_dir=str(profile_dir),
        )
        await asyncio.to_thread(
            bootstrap_login,
            str(profile_dir),
            args.chrome_executable,
        )
        scout_debug_event(
            "keyword_scout_login_bootstrap_completed",
            profile_dir=str(profile_dir),
        )
        await open_browser_runtime()

    try:
        await open_browser_runtime()

        while True:
            await _flush_pending_quote_volumes(client, history)
            search_url = (
                "https://www.pinterest.com/search/pins/?q="
                + quote_plus(args.seed_query)
            )
            try:
                if page is None:
                    await open_browser_runtime()
                response = await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                guard_pinterest_response(response)
                await wait_for_pin_growth(
                    page,
                    previous_count=0,
                    timeout_ms=INITIAL_RESULTS_TIMEOUT_MS,
                )
                await _wait_for_pinterest_access(page)
                initial_dwell = await paced_wait(page, pace.initial_dwell_ms)
                scout_debug_event(
                    "keyword_scout_cycle_started",
                    query=args.seed_query,
                    initial_dwell_ms=initial_dwell,
                    seen_pins=len(history.seen_pins),
                    seen_quotes=len(history.seen_quotes),
                )

                processed = 0
                extracted = 0
                saved_quotes = 0
                empty_batch_streak = 0
                cycle_seen: set[str] = set()
                for batch in range(args.max_scroll_batches):
                    await paced_wait(page, pace.inspect_dwell_ms)
                    visible = await extract_visible(page)
                    if visible:
                        empty_batch_streak = 0
                    else:
                        empty_batch_streak += 1
                    if empty_batch_streak >= EMPTY_BATCH_RELOAD_THRESHOLD:
                        diagnostics = await _search_page_diagnostics(page)
                        gate = await access_gate(page)
                        scout_debug_event(
                            "keyword_scout_empty_search_reload",
                            query=args.seed_query,
                            batch_index=batch + 1,
                            gate=gate,
                            **diagnostics,
                        )
                        print(
                            "Pinterest search has no extractable Pins; reloading. "
                            + "url="
                            + str(diagnostics.get("url") or page.url)
                            + " pin_links="
                            + str(diagnostics.get("pin_links") or 0)
                            + " pinimg_images="
                            + str(diagnostics.get("pinimg_images") or 0)
                        )
                        if gate is not None:
                            raise PinterestAccessGateError(gate)
                        response = await page.reload(
                            wait_until="domcontentloaded",
                            timeout=60_000,
                        )
                        guard_pinterest_response(response)
                        await wait_for_pin_growth(
                            page,
                            previous_count=0,
                            timeout_ms=INITIAL_RESULTS_TIMEOUT_MS,
                        )
                        empty_batch_streak = 0
                        await paced_wait(page, pace.inspect_dwell_ms)
                        visible = await extract_visible(page)
                    candidates = []
                    persistent_pins = history.seen_pins
                    persistent_assets = history.seen_assets
                    for candidate in visible:
                        pin_key = pin_history_key(candidate.pin_url)
                        asset_key = pinimg_asset_key(candidate.image_url)
                        if not pin_key or pin_key in cycle_seen:
                            continue
                        cycle_seen.add(pin_key)
                        if pin_key in persistent_pins:
                            continue
                        if asset_key and asset_key in persistent_assets:
                            continue
                        candidates.append(candidate)

                    remaining = args.max_pins_per_cycle - processed
                    candidates = candidates[:max(0, remaining)]
                    if candidates:
                        resolved = await resolve_pin_details(
                            page,
                            candidates,
                            detail_page=detail_page,
                            concurrency=1,
                        )
                        for candidate in resolved:
                            if processed >= args.max_pins_per_cycle:
                                break
                            processed += 1
                            scout_debug_event(
                                "keyword_scout_pin_processing",
                                pin_url=candidate.pin_url,
                                image_url=candidate.image_url,
                                batch_index=batch + 1,
                                processed=processed,
                            )
                            try:
                                result = await client.extract_quote(candidate)
                            except httpx.HTTPStatusError as exc:
                                status = exc.response.status_code
                                # Only permanently skip a Pin when the request
                                # itself is invalid/non-processable. Endpoint
                                # absence, auth failures, throttling and server
                                # failures must remain retryable so deployment
                                # or transient outages never burn Pinterest
                                # history.
                                if _quote_extract_status_is_terminal(status):
                                    history.remember_candidate(
                                        candidate.pin_url,
                                        candidate.image_url,
                                    )
                                retryable = not _quote_extract_status_is_terminal(status)
                                scout_debug_event(
                                    "keyword_scout_quote_extract_failed",
                                    pin_url=candidate.pin_url,
                                    image_url=candidate.image_url,
                                    status_code=status,
                                    retryable=retryable,
                                )
                                print(
                                    "quote_extract_failed status="
                                    + str(status)
                                    + " retry="
                                    + ("yes" if retryable else "no")
                                    + " pin="
                                    + candidate.pin_url
                                )
                                continue
                            except Exception as exc:
                                scout_debug_event(
                                    "keyword_scout_quote_extract_failed",
                                    pin_url=candidate.pin_url,
                                    image_url=candidate.image_url,
                                    error_type=exc.__class__.__name__,
                                    error=str(exc)[:500],
                                    retryable=True,
                                )
                                print(
                                    "quote_extract_retry_later error="
                                    + exc.__class__.__name__
                                    + " pin="
                                    + candidate.pin_url
                                )
                                continue

                            quotes = _dedupe([
                                str(value)
                                for value in (result.get("quotes") or [])
                            ])
                            new_quotes = [
                                quote
                                for quote in quotes
                                if quote.casefold() not in history.known_quote_keys
                            ]
                            scout_debug_event(
                                "keyword_scout_quote_extracted",
                                pin_url=candidate.pin_url,
                                image_url=candidate.image_url,
                                quotes=quotes,
                                new_quotes=new_quotes,
                                quote_count=len(quotes),
                                new_quote_count=len(new_quotes),
                                confidence=float(result.get("confidence") or 0.0),
                                provider=str(result.get("provider") or ""),
                                model=str(result.get("model") or ""),
                            )
                            history.remember_candidate(
                                candidate.pin_url,
                                candidate.image_url,
                            )
                            if not new_quotes:
                                scout_debug_event(
                                    "keyword_scout_quote_skipped",
                                    pin_url=candidate.pin_url,
                                    image_url=candidate.image_url,
                                    reason="none_or_seen",
                                    quotes=quotes,
                                )
                                print(
                                    "pin="
                                    + candidate.pin_url
                                    + " quote=none_or_seen"
                                )
                                continue

                            extracted += 1
                            try:
                                volume = await client.resolve_volume(new_quotes)
                            except Exception as exc:
                                history.remember_pending_quotes(new_quotes)
                                scout_debug_event(
                                    "keyword_scout_volume_deferred",
                                    pin_url=candidate.pin_url,
                                    quotes=new_quotes,
                                    error_type=exc.__class__.__name__,
                                    error=str(exc)[:500],
                                )
                                print(
                                    "volume_retry_later quote="
                                    + " | ".join(new_quotes)
                                    + " error="
                                    + exc.__class__.__name__
                                )
                                continue

                            history.remember_quotes(new_quotes)
                            saved_quotes += len(new_quotes)
                            items = volume.get("items") or []
                            volume_by_keyword = {
                                str(item.get("keyword") or "").casefold(): int(
                                    item.get("search_volume") or 0
                                )
                                for item in items
                                if isinstance(item, dict)
                            }
                            for quote in new_quotes:
                                print(
                                    "quote="
                                    + quote
                                    + " volume="
                                    + str(volume_by_keyword.get(quote.casefold(), 0))
                                    + "/mo"
                                )
                            scout_debug_event(
                                "keyword_scout_quotes_saved",
                                pin_url=candidate.pin_url,
                                image_url=candidate.image_url,
                                quote_count=len(new_quotes),
                                quotes=new_quotes,
                                volumes=[
                                    {
                                        "keyword": str(item.get("keyword") or ""),
                                        "search_volume": int(item.get("search_volume") or 0),
                                        "competition": str(item.get("competition") or ""),
                                    }
                                    for item in items
                                    if isinstance(item, dict)
                                ],
                                provider_requested=int(
                                    volume.get("provider_requested") or 0
                                ),
                                cached=int(volume.get("cached") or 0),
                            )
                            await page.wait_for_timeout(
                                random.randint(*pace.submit_pause_ms)
                            )

                    scout_debug_event(
                        "keyword_scout_batch_completed",
                        query=args.seed_query,
                        batch_index=batch + 1,
                        batch_total=args.max_scroll_batches,
                        visible=len(visible),
                        fresh=len(candidates),
                        processed=processed,
                        quote_pins=extracted,
                        saved_quotes=saved_quotes,
                    )
                    print(
                        "keyword_cycle batch="
                        + str(batch + 1)
                        + "/"
                        + str(args.max_scroll_batches)
                        + " visible="
                        + str(len(visible))
                        + " fresh="
                        + str(len(candidates))
                        + " processed="
                        + str(processed)
                        + " quote_pins="
                        + str(extracted)
                        + " saved_quotes="
                        + str(saved_quotes)
                    )
                    if processed >= args.max_pins_per_cycle:
                        break
                    await _scroll_search_page(page, pace)

                scout_debug_event(
                    "keyword_scout_cycle_completed",
                    query=args.seed_query,
                    processed=processed,
                    quote_pins=extracted,
                    saved_quotes=saved_quotes,
                    cycle_seconds=args.cycle_seconds,
                )
                if args.once:
                    return
                next_cycle_seconds = (
                    args.cycle_seconds
                    if processed > 0
                    else min(args.cycle_seconds, EMPTY_CYCLE_RETRY_SECONDS)
                )
                print(
                    "Keyword Scout cycle complete. "
                    + "processed="
                    + str(processed)
                    + " saved_quotes="
                    + str(saved_quotes)
                    + " next_cycle_in="
                    + str(next_cycle_seconds)
                    + "s"
                    + (" (empty-search fast retry)" if processed == 0 else "")
                )
                await asyncio.sleep(next_cycle_seconds)
            except PinterestRateLimitedError:
                wait_seconds = max(args.cycle_seconds, 600)
                print(
                    "Pinterest rate limited Keyword Scout; waiting "
                    + str(wait_seconds)
                    + "s."
                )
                scout_debug_event(
                    "keyword_scout_rate_limited",
                    wait_seconds=wait_seconds,
                )
                if args.once:
                    raise
                await asyncio.sleep(wait_seconds)
            except PinterestAccessGateError as exc:
                if exc.gate == "login":
                    await bootstrap_keyword_login()
                    continue
                if page is None:
                    await open_browser_runtime()
                    continue
                await _wait_for_pinterest_access(page)
            except Exception as exc:
                if args.once:
                    raise
                scout_debug_event(
                    "keyword_scout_cycle_recovering",
                    query=args.seed_query,
                    error_type=exc.__class__.__name__,
                    error=str(exc)[:500],
                    retry_seconds=RUNTIME_RECOVERY_SECONDS,
                )
                print(
                    "Keyword Scout runtime error="
                    + exc.__class__.__name__
                    + "; recovering in "
                    + str(RUNTIME_RECOVERY_SECONDS)
                    + "s. The continuous loop remains active."
                )
                await close_browser_runtime()
                await asyncio.sleep(RUNTIME_RECOVERY_SECONDS)
                continue
    finally:
        await close_browser_runtime()
        await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 0 Quote Scout. In --auto-pinterest mode it searches Pinterest "
            "for Saying Trucker hat images, reads visible hat quotes through CAM "
            "vision analysis, resolves Google Ads volume through AEBrowse, and "
            "stores results in Stage 0."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("RRUGC_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--agent-id",
        default=os.getenv("RRUGC_AGENT_ID", ""),
    )
    parser.add_argument(
        "--token",
        default=os.getenv("RRUGC_SCOUT_TOKEN", ""),
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Manual mode: keyword to resolve. Repeat for multiple keywords.",
    )
    parser.add_argument(
        "--keywords-file",
        help="Manual mode: one keyword per line or a JSON array.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--auto-pinterest", action="store_true")
    parser.add_argument("--seed-query", default=DEFAULT_PINTEREST_QUERY)
    parser.add_argument(
        "--profile-dir",
        default=os.getenv(
            "RRUGC_KEYWORD_PROFILE_DIR",
            "./pinterest-profile-keyword",
        ),
    )
    parser.add_argument(
        "--pace",
        choices=tuple(SCOUT_PACES),
        default=os.getenv("RRUGC_PACE", "careful"),
    )
    parser.add_argument(
        "--chrome-executable",
        default=os.getenv("RRUGC_CHROME_EXECUTABLE", ""),
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--cycle-seconds",
        type=int,
        default=DEFAULT_CYCLE_SECONDS,
    )
    parser.add_argument(
        "--max-scroll-batches",
        type=int,
        default=DEFAULT_MAX_SCROLL_BATCHES,
    )
    parser.add_argument(
        "--max-pins-per-cycle",
        type=int,
        default=DEFAULT_MAX_PINS_PER_CYCLE,
    )
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.agent_id:
        parser.error("--agent-id or RRUGC_AGENT_ID is required")
    if not args.token:
        parser.error("--token or RRUGC_SCOUT_TOKEN is required")

    if args.auto_pinterest:
        if args.cycle_seconds < 30:
            parser.error("--cycle-seconds must be at least 30")
        if args.max_scroll_batches < 1 or args.max_scroll_batches > 50:
            parser.error("--max-scroll-batches must be between 1 and 50")
        if args.max_pins_per_cycle < 1 or args.max_pins_per_cycle > 200:
            parser.error("--max-pins-per-cycle must be between 1 and 200")
        asyncio.run(run_pinterest_quote_scout(args))
        return 0

    keywords = _dedupe(list(args.keyword) + _keywords_from_file(args.keywords_file))
    if not keywords:
        parser.error(
            "Manual mode requires --keyword/--keywords-file; "
            "use --auto-pinterest for autonomous quote scouting."
        )
    if len(keywords) > 50:
        parser.error("At most 50 unique keywords may be submitted per request")

    try:
        payload = resolve_keyword_volume(
            base_url=args.base_url,
            agent_id=args.agent_id,
            token=args.token,
            keywords=keywords,
            force=args.force,
        )
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        print(f"Keyword volume request failed: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(
        "Keyword volume:",
        f"requested={payload.get('requested', 0)}",
        f"provider={payload.get('provider_requested', 0)}",
        f"cached={payload.get('cached', 0)}",
    )
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        volume = int(item.get("search_volume") or 0)
        competition = str(item.get("competition") or "-")
        cpc_low = item.get("cpc_low")
        cpc_high = item.get("cpc_high")
        cpc = (
            "$" + f"{float(cpc_low):.2f}"
            + "-$" + f"{float(cpc_high):.2f}"
            if cpc_low is not None and cpc_high is not None
            else "-"
        )
        print(
            f"{volume:>8,}/mo  {competition:<8}  {cpc:<13}  "
            + str(item.get("keyword") or "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
