from __future__ import annotations

import argparse
import ast
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
    Candidate,
    PIN_RELATED_SCAN_LIMIT,
    PinterestAccessGateError,
    PinterestRateLimitedError,
    SCOUT_PACES,
    _looks_like_browser_runtime_failure,
    access_gate,
    allowed_image,
    bootstrap_login,
    configure_scout_debug_log,
    configure_scout_remote_log,
    extract_related_candidates,
    extract_visible_pin_candidates,
    guard_pinterest_response,
    launch_context,
    loaded_pin_count,
    paced_wait,
    pin_history_key,
    pinimg_asset_key,
    resolve_pin_details,
    scout_debug_event,
    shutdown_scout_remote_log,
    wait_for_pin_growth,
)


DEFAULT_BASE_URL = "https://creative-assets.ddns.net"
DEFAULT_PINTEREST_QUERY = "Saying Trucker hat"
DEFAULT_HISTORY_FILENAME = "keyword-scout-history.json"
DEFAULT_CYCLE_SECONDS = 180
DEFAULT_MAX_SCROLL_BATCHES = 10
DEFAULT_MAX_PINS_PER_CYCLE = 40
DEFAULT_RELATED_PER_PIN = 60
KEYWORD_SCROLL_STEP_PX = (260, 420)
KEYWORD_SCROLL_STEPS_PER_BATCH = (1, 2)
KEYWORD_SCROLL_STEP_PAUSE_MS = (1_100, 1_700)
KEYWORD_SCROLL_SETTLE_MS = (900, 1_500)
KEYWORD_SCROLL_GROWTH_TIMEOUT_MS = 4_500
INITIAL_RESULTS_TIMEOUT_MS = 12_000
EMPTY_BATCH_RELOAD_THRESHOLD = 3
EMPTY_CYCLE_RETRY_SECONDS = 30
RUNTIME_RECOVERY_SECONDS = 30
CAM_DEFAULT_REQUEST_TIMEOUT_SECONDS = 45.0
CAM_QUOTE_REQUEST_TIMEOUT_SECONDS = 90.0


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


def _keyword_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("phrase") or ""
    text = str(value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        parsed: Any = None
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(text)
                break
            except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
                continue
        if isinstance(parsed, dict):
            text = str(parsed.get("text") or parsed.get("phrase") or "")
        else:
            match = re.search(
                r"""['"](?:text|phrase)['"]\s*:\s*(['"])(.*?)\1\s*(?:,\s*['"][^'"]+['"]\s*:|})""",
                text,
                flags=re.DOTALL,
            )
            if match:
                text = match.group(2)
    return text


def _clean_keyword(value: Any) -> str:
    return re.sub(r"\s+", " ", _keyword_text(value)).strip(" \t\r\n\"'“”")


_KEYWORD_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
KEYWORD_MIN_WORDS = 2


def _keyword_word_count(value: str) -> int:
    return len(_KEYWORD_WORD_RE.findall(value))


def _dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_keyword(value)
        if not clean or _keyword_word_count(clean) < KEYWORD_MIN_WORDS:
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
    keywords = _dedupe(keywords)
    if not keywords:
        return {
            "requested": 0,
            "provider_requested": 0,
            "cached": 0,
            "items": [],
        }
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
            "version": 2,
            "seen_pins": [],
            "seen_assets": [],
            "expanded_pins": [],
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
    def expanded_pins(self) -> set[str]:
        return {
            pin_history_key(value)
            for value in self._set("expanded_pins")
            if pin_history_key(value)
        }

    @property
    def seen_quotes(self) -> set[str]:
        return {
            value.casefold()
            for value in _dedupe(list(self._set("seen_quotes")))
        }

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

    def remember_expanded_pin(self, pin_url: str) -> None:
        expanded = list(self._set("expanded_pins"))
        pin = pin_history_key(pin_url)
        if pin and pin not in expanded:
            expanded.append(pin)
        self.data["expanded_pins"] = expanded[-50_000:]
        self.data["version"] = 2
        self.save()

    def remember_quotes(self, quotes: list[str]) -> None:
        existing = _dedupe(list(self._set("seen_quotes")))
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
        timeout_seconds: float = CAM_DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            started = time.monotonic()
            try:
                response = await self.client.post(
                    path,
                    json=payload,
                    timeout=httpx.Timeout(timeout_seconds, connect=10.0),
                )
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
                duration_ms = round((time.monotonic() - started) * 1000)
                error_code = None
                error_message = str(exc)[:500]
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response is not None
                ):
                    try:
                        response_payload = exc.response.json()
                    except (ValueError, TypeError):
                        response_payload = None
                    if isinstance(response_payload, dict):
                        detail = response_payload.get("detail")
                        if isinstance(detail, dict):
                            error_code = str(detail.get("code") or "").strip() or None
                            error_message = (
                                str(detail.get("message") or error_message).strip()[:500]
                            )
                        elif detail is not None:
                            error_message = str(detail).strip()[:500]
                scout_debug_event(
                    "keyword_scout_cam_request_failed",
                    operation=operation,
                    status_code=status,
                    error_type=exc.__class__.__name__,
                    error_code=error_code,
                    error=error_message,
                    duration_ms=duration_ms,
                    request_timeout_seconds=timeout_seconds,
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
                    if status in {429, 503}:
                        retry_delay = 5.0 * (2 ** attempt) + random.random()
                    else:
                        retry_delay = 1.0 * (2 ** attempt) + random.random()
                    scout_debug_event(
                        "keyword_scout_cam_retry_wait",
                        operation=operation,
                        status_code=status,
                        retry_delay_seconds=round(retry_delay, 2),
                        next_attempt=attempt + 2,
                    )
                    await asyncio.sleep(retry_delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{operation} failed after retries")

    async def extract_quote(self, candidate) -> dict[str, Any]:
        return await self._post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/quote-analysis/extract",
            {
                "pin_url": candidate.pin_url,
                "image_url": candidate.image_url,
                "alt_text": candidate.alt_text,
            },
            operation="extract_hat_quote",
            timeout_seconds=CAM_QUOTE_REQUEST_TIMEOUT_SECONDS,
        )

    async def resolve_volume(
        self,
        quotes: list[str],
        *,
        source_image_url: str | None = None,
        source_pin_url: str | None = None,
    ) -> dict[str, Any]:
        quotes = _dedupe(quotes)
        if not quotes:
            return {
                "requested": 0,
                "provider_requested": 0,
                "cached": 0,
                "items": [],
            }
        payload: dict[str, Any] = {"keywords": quotes, "force": False}
        if source_image_url:
            payload["source_image_url"] = source_image_url
        if source_pin_url:
            payload["source_pin_url"] = source_pin_url
        return await self._post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/keyword-analysis/resolve",
            payload,
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
    previous_count = await loaded_pin_count(page)
    steps = random.randint(*KEYWORD_SCROLL_STEPS_PER_BATCH)
    for _ in range(steps):
        await page.mouse.wheel(0, random.randint(*KEYWORD_SCROLL_STEP_PX))
        await page.wait_for_timeout(
            random.randint(*KEYWORD_SCROLL_STEP_PAUSE_MS)
        )
    latest_count = await wait_for_pin_growth(
        page,
        previous_count=previous_count,
        timeout_ms=KEYWORD_SCROLL_GROWTH_TIMEOUT_MS,
    )
    settle_ms = random.randint(*KEYWORD_SCROLL_SETTLE_MS)
    await page.wait_for_timeout(settle_ms)
    scout_debug_event(
        "keyword_scout_scroll_completed",
        pace=pace.name,
        steps=steps,
        previous_pin_links=previous_count,
        latest_pin_links=latest_count,
        settle_ms=settle_ms,
    )


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
          pin_links_with_direct_image: Array.from(
            document.querySelectorAll('a[href*="/pin/"]')
          ).filter((node) => node.querySelector(
            'img[src*="pinimg.com"], img[srcset*="pinimg.com"]'
          )).length,
          pinimg_with_pin_ancestor: Array.from(
            document.querySelectorAll(
              'img[src*="pinimg.com"], img[srcset*="pinimg.com"]'
            )
          ).filter((node) => node.closest('a[href*="/pin/"]')).length,
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


async def _process_keyword_candidate(
    client: QuoteScoutClient,
    history: KeywordScoutHistory,
    candidate: Candidate,
    *,
    source: str,
    root_pin_url: str,
) -> tuple[int, int]:
    scout_debug_event(
        "keyword_scout_pin_processing",
        source=source,
        root_pin_url=root_pin_url,
        pin_url=candidate.pin_url,
        image_url=candidate.image_url,
    )
    try:
        result = await client.extract_quote(candidate)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if _quote_extract_status_is_terminal(status):
            history.remember_candidate(candidate.pin_url, candidate.image_url)
        retryable = not _quote_extract_status_is_terminal(status)
        scout_debug_event(
            "keyword_scout_quote_extract_failed",
            source=source,
            root_pin_url=root_pin_url,
            pin_url=candidate.pin_url,
            image_url=candidate.image_url,
            status_code=status,
            retryable=retryable,
        )
        print(
            "quote_extract_failed source="
            + source
            + " status="
            + str(status)
            + " retry="
            + ("yes" if retryable else "no")
            + " pin="
            + candidate.pin_url
        )
        return 0, 0
    except Exception as exc:
        scout_debug_event(
            "keyword_scout_quote_extract_failed",
            source=source,
            root_pin_url=root_pin_url,
            pin_url=candidate.pin_url,
            image_url=candidate.image_url,
            error_type=exc.__class__.__name__,
            error=str(exc)[:500],
            retryable=True,
        )
        print(
            "quote_extract_retry_later source="
            + source
            + " error="
            + exc.__class__.__name__
            + " pin="
            + candidate.pin_url
        )
        return 0, 0

    raw_quotes = list(result.get("quotes") or [])
    rejected_min_words = [
        clean
        for raw in raw_quotes
        if (clean := _clean_keyword(raw))
        and _keyword_word_count(clean) < KEYWORD_MIN_WORDS
    ]
    quotes = _dedupe(raw_quotes)
    if rejected_min_words:
        scout_debug_event(
            "keyword_scout_keyword_rejected",
            source=source,
            root_pin_url=root_pin_url,
            pin_url=candidate.pin_url,
            reason="min_words",
            minimum_words=KEYWORD_MIN_WORDS,
            rejected_keywords=rejected_min_words,
            rejected_count=len(rejected_min_words),
        )
        print(
            "keyword_rejected reason=min_words minimum="
            + str(KEYWORD_MIN_WORDS)
            + " value="
            + " | ".join(rejected_min_words)
        )
    new_quotes = [
        quote
        for quote in quotes
        if quote.casefold() not in history.known_quote_keys
    ]
    scout_debug_event(
        "keyword_scout_quote_extracted",
        source=source,
        root_pin_url=root_pin_url,
        pin_url=candidate.pin_url,
        image_url=candidate.image_url,
        quotes=quotes,
        new_quotes=new_quotes,
        quote_count=len(quotes),
        new_quote_count=len(new_quotes),
        is_target_cap=bool(result.get("is_target_cap", False)),
        confidence=float(result.get("confidence") or 0.0),
        provider=str(result.get("provider") or ""),
        model=str(result.get("model") or ""),
    )
    history.remember_candidate(candidate.pin_url, candidate.image_url)
    if not new_quotes:
        scout_debug_event(
            "keyword_scout_quote_skipped",
            source=source,
            root_pin_url=root_pin_url,
            pin_url=candidate.pin_url,
            image_url=candidate.image_url,
            reason=(
                "not_target_cap"
                if not bool(result.get("is_target_cap", False))
                else "none_or_seen"
            ),
            is_target_cap=bool(result.get("is_target_cap", False)),
            quotes=quotes,
        )
        skip_reason = (
            "not_target_cap"
            if not bool(result.get("is_target_cap", False))
            else "none_or_seen"
        )
        print(
            "pin="
            + candidate.pin_url
            + " source="
            + source
            + " quote="
            + skip_reason
        )
        return 0, 0

    try:
        volume = await client.resolve_volume(
            new_quotes,
            source_image_url=candidate.image_url,
            source_pin_url=candidate.pin_url,
        )
    except Exception as exc:
        history.remember_pending_quotes(new_quotes)
        scout_debug_event(
            "keyword_scout_volume_deferred",
            source=source,
            root_pin_url=root_pin_url,
            pin_url=candidate.pin_url,
            quotes=new_quotes,
            error_type=exc.__class__.__name__,
            error=str(exc)[:500],
        )
        print(
            "volume_retry_later quote="
            + " | ".join(new_quotes)
            + " source="
            + source
            + " error="
            + exc.__class__.__name__
        )
        return 1, 0

    history.remember_quotes(new_quotes)
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
            + " source="
            + source
        )
    scout_debug_event(
        "keyword_scout_quotes_saved",
        source=source,
        root_pin_url=root_pin_url,
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
        provider_requested=int(volume.get("provider_requested") or 0),
        cached=int(volume.get("cached") or 0),
    )
    return 1, len(new_quotes)


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
    configure_scout_remote_log(
        base_url=args.base_url,
        agent_id=args.agent_id,
        token=args.token,
    )

    print("Stage 0 Keyword Scout log: " + str(log_path))
    print("Remote Scout log      : API enabled · retention 5 days")
    print("Pinterest query       : " + args.seed_query)
    print("Pinterest profile     : " + str(profile_dir))
    print("Related per Pin       : " + str(args.related_per_pin))
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
        related_per_pin=args.related_per_pin,
        history_path=str(history.path),
    )
    scout_debug_event(
        "keyword_scout_remote_logging_enabled",
        retention_days=5,
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
                related_scanned = 0
                related_fresh = 0
                related_processed = 0
                empty_batch_streak = 0
                cycle_seen: set[str] = set()
                for batch in range(args.max_scroll_batches):
                    await paced_wait(page, pace.inspect_dwell_ms)
                    visible = await extract_visible_pin_candidates(page)
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
                            + " direct_pairs="
                            + str(
                                diagnostics.get(
                                    "pin_links_with_direct_image"
                                )
                                or 0
                            )
                            + " image_ancestors="
                            + str(
                                diagnostics.get(
                                    "pinimg_with_pin_ancestor"
                                )
                                or 0
                            )
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
                        visible = await extract_visible_pin_candidates(page)
                    candidates = []
                    persistent_pins = history.seen_pins
                    persistent_assets = history.seen_assets
                    expanded_pins = history.expanded_pins
                    for candidate in visible:
                        pin_key = pin_history_key(candidate.pin_url)
                        asset_key = pinimg_asset_key(candidate.image_url)
                        if not pin_key or pin_key in cycle_seen:
                            continue
                        cycle_seen.add(pin_key)
                        needs_expansion = pin_key not in expanded_pins
                        if pin_key in persistent_pins and not needs_expansion:
                            continue
                        if (
                            asset_key
                            and asset_key in persistent_assets
                            and not needs_expansion
                        ):
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
                            fallback_on_error=False,
                        )
                        for candidate in resolved:
                            if processed >= args.max_pins_per_cycle:
                                break
                            pin_key = pin_history_key(candidate.pin_url)
                            asset_key = pinimg_asset_key(candidate.image_url)
                            needs_expansion = pin_key not in history.expanded_pins
                            root_needs_quote = (
                                pin_key not in history.seen_pins
                                and (
                                    not asset_key
                                    or asset_key not in history.seen_assets
                                )
                            )
                            if not allowed_image(candidate.image_url):
                                scout_debug_event(
                                    "keyword_scout_pin_detail_unresolved",
                                    source="root",
                                    pin_url=candidate.pin_url,
                                    root_pin_url=candidate.pin_url,
                                )
                                continue
                            if not needs_expansion and not root_needs_quote:
                                continue

                            processed += 1
                            related_rows: list[Candidate] = []
                            if args.related_per_pin > 0:
                                try:
                                    related_rows = await extract_related_candidates(
                                        detail_page or page,
                                        candidate,
                                        pace=pace,
                                        limit=args.related_per_pin,
                                    )
                                except (
                                    PinterestAccessGateError,
                                    PinterestRateLimitedError,
                                ):
                                    raise
                                except Exception as exc:
                                    if _looks_like_browser_runtime_failure(exc):
                                        raise
                                    scout_debug_event(
                                        "keyword_scout_related_scan_failed",
                                        root_pin_url=candidate.pin_url,
                                        error_type=exc.__class__.__name__,
                                        error=str(exc)[:500],
                                    )
                                    print(
                                        "related_scan_failed root_pin="
                                        + candidate.pin_url
                                        + " error="
                                        + exc.__class__.__name__
                                    )

                            related_scanned += len(related_rows)
                            fresh_related: list[Candidate] = []
                            persistent_pins = history.seen_pins
                            persistent_assets = history.seen_assets
                            for related in related_rows:
                                related_pin_key = pin_history_key(related.pin_url)
                                related_asset_key = pinimg_asset_key(related.image_url)
                                if not related_pin_key or related_pin_key in cycle_seen:
                                    continue
                                if related_pin_key in persistent_pins:
                                    continue
                                if (
                                    related_asset_key
                                    and related_asset_key in persistent_assets
                                ):
                                    continue
                                cycle_seen.add(related_pin_key)
                                fresh_related.append(related)

                            related_fresh += len(fresh_related)
                            resolved_related: list[Candidate] = []
                            if fresh_related:
                                resolved_related = await resolve_pin_details(
                                    page,
                                    fresh_related,
                                    detail_page=detail_page,
                                    concurrency=1,
                                    fallback_on_error=False,
                                )
                            valid_resolved_related = [
                                row
                                for row in resolved_related
                                if allowed_image(row.image_url)
                            ]
                            expansion_complete = (
                                args.related_per_pin <= 0
                                or len(related_rows) > 0
                            )
                            if expansion_complete:
                                history.remember_expanded_pin(candidate.pin_url)
                                scout_debug_event(
                                    "keyword_scout_root_expanded",
                                    root_pin_url=candidate.pin_url,
                                    scanned=len(related_rows),
                                    fresh=len(fresh_related),
                                    resolved=len(valid_resolved_related),
                                )
                            else:
                                scout_debug_event(
                                    "keyword_scout_root_expansion_incomplete",
                                    root_pin_url=candidate.pin_url,
                                    requested=args.related_per_pin,
                                    scanned=0,
                                )
                            scout_debug_event(
                                "keyword_scout_related_scanned",
                                root_pin_url=candidate.pin_url,
                                requested=args.related_per_pin,
                                scanned=len(related_rows),
                                fresh=len(fresh_related),
                                resolved=len(valid_resolved_related),
                            )
                            print(
                                "root_pin="
                                + candidate.pin_url
                                + " related_scanned="
                                + str(len(related_rows))
                                + "/"
                                + str(args.related_per_pin)
                                + " related_fresh="
                                + str(len(fresh_related))
                            )

                            processing_queue = []
                            if root_needs_quote:
                                processing_queue.append(
                                    ("root", candidate, candidate.pin_url)
                                )
                            processing_queue.extend(
                                (
                                    "related",
                                    related,
                                    candidate.pin_url,
                                )
                                for related in valid_resolved_related
                            )
                            for source, row, root_pin_url in processing_queue:
                                if source == "related":
                                    row_pin_key = pin_history_key(row.pin_url)
                                    row_asset_key = pinimg_asset_key(row.image_url)
                                    if row_pin_key in history.seen_pins:
                                        continue
                                    if (
                                        row_asset_key
                                        and row_asset_key in history.seen_assets
                                    ):
                                        continue
                                    related_processed += 1

                                quote_delta, saved_delta = (
                                    await _process_keyword_candidate(
                                        client,
                                        history,
                                        row,
                                        source=source,
                                        root_pin_url=root_pin_url,
                                    )
                                )
                                extracted += quote_delta
                                saved_quotes += saved_delta
                                await page.wait_for_timeout(
                                    random.randint(*pace.submit_pause_ms)
                                )

                        search_to_front = getattr(page, "bring_to_front", None)
                        if callable(search_to_front):
                            await search_to_front()

                    scout_debug_event(
                        "keyword_scout_batch_completed",
                        query=args.seed_query,
                        batch_index=batch + 1,
                        batch_total=args.max_scroll_batches,
                        visible=len(visible),
                        fresh=len(candidates),
                        processed=processed,
                        related_scanned=related_scanned,
                        related_fresh=related_fresh,
                        related_processed=related_processed,
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
                        + " related="
                        + str(related_processed)
                        + "/"
                        + str(related_scanned)
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
                    related_scanned=related_scanned,
                    related_fresh=related_fresh,
                    related_processed=related_processed,
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
                browser_failure = _looks_like_browser_runtime_failure(exc)
                retry_seconds = 2 if browser_failure else RUNTIME_RECOVERY_SECONDS
                event_name = (
                    "keyword_scout_browser_recycled"
                    if browser_failure
                    else "keyword_scout_cycle_recovering"
                )
                scout_debug_event(
                    event_name,
                    query=args.seed_query,
                    error_type=exc.__class__.__name__,
                    error=str(exc)[:500],
                    retry_seconds=retry_seconds,
                )
                if browser_failure:
                    print(
                        "Keyword Scout browser/detail tab closed unexpectedly; "
                        + "recycling Chrome context in "
                        + str(retry_seconds)
                        + "s."
                    )
                else:
                    print(
                        "Keyword Scout runtime error="
                        + exc.__class__.__name__
                        + "; recovering in "
                        + str(retry_seconds)
                        + "s. The continuous loop remains active."
                    )
                await close_browser_runtime()
                await asyncio.sleep(retry_seconds)
                continue
    finally:
        await close_browser_runtime()
        shutdown_scout_remote_log(timeout_seconds=3.0)
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
    parser.add_argument(
        "--related-per-pin",
        type=int,
        default=DEFAULT_RELATED_PER_PIN,
        help="Scan up to this many related Pins from each root Pin detail page.",
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
        if args.related_per_pin < 0 or args.related_per_pin > PIN_RELATED_SCAN_LIMIT:
            parser.error(
                "--related-per-pin must be between 0 and "
                + str(PIN_RELATED_SCAN_LIMIT)
            )
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
