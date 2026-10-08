from __future__ import annotations

import argparse
import ast
import asyncio
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import random
import re
import socket
import sys
import time
from uuid import uuid4
from typing import Any
from urllib.parse import quote_plus

import httpx

from scout import (
    CLIENT_VERSION,
    Candidate,
    PIN_RELATED_HARD_LIMIT,
    PIN_RELATED_SCAN_LIMIT,
    PinterestAccessGateError,
    PinterestRateLimitedError,
    SCOUT_PACES,
    SCOUT_FATAL_EXIT_CODE,
    SCOUT_MAX_AUTOMATIC_RESTARTS,
    SCOUT_RUNTIME_ERRORS_BEFORE_RESTART,
    ScoutFatalStop,
    ScoutRestartRequested,
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
    mark_pinterest_login_ready,
    clear_pinterest_login_ready,
    pinterest_login_ready,
    paced_wait,
    pin_history_key,
    pinimg_asset_key,
    report_scout_fatal_status,
    resolve_pin_details,
    scout_debug_event,
    scout_restart_delay_seconds,
    shutdown_scout_remote_log,
    startup_access_gate,
    wait_for_pin_growth,
)


DEFAULT_BASE_URL = "https://creative-assets.ddns.net"
DEFAULT_PINTEREST_QUERY = "Saying Trucker hat"
DEFAULT_HISTORY_FILENAME = "keyword-scout-history.json"
DEFAULT_CYCLE_SECONDS = 180
DEFAULT_MAX_SCROLL_BATCHES = 10
DEFAULT_MAX_PINS_PER_CYCLE = 40
DEFAULT_RELATED_PER_PIN = 60
DEFAULT_DEEP_DIVE_RELATED_PER_PIN = 150
DEFAULT_DEEP_DIVE_SEEDS_PER_CYCLE = 3
DEFAULT_DEEP_DIVE_MIN_SEARCH_VOLUME = 1_000
DEFAULT_DEEP_DIVE_STYLE_MAX_DEPTH = 3
DEFAULT_DEEP_DIVE_MARKET_MAX_DEPTH = 4
MARKET_DEEP_DIVE_SCORE_THRESHOLD = 0.65
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
BROWSER_FALLBACK_MAX_IMAGE_BYTES = 12 * 1024 * 1024
KEYWORD_QUOTE_MIN_PRIORITY_SCORE = 0.60
KEYWORD_QUOTE_HIGH_PRIORITY_SCORE = 0.85
KEYWORD_CAPACITY_PREFLIGHT_RETRY_SECONDS = 180


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


def _quote_priority_score(result: dict[str, Any]) -> float:
    try:
        value = float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(1.0, value))


def _quote_priority_label(score: float) -> str:
    if score >= KEYWORD_QUOTE_HIGH_PRIORITY_SCORE:
        return "high"
    if score >= KEYWORD_QUOTE_MIN_PRIORITY_SCORE:
        return "normal"
    return "low"


@dataclass(frozen=True)
class KeywordCandidateResult:
    quote_delta: int = 0
    saved_delta: int = 0
    priority_score: float = 0.0
    quotes: tuple[str, ...] = ()
    max_search_volume: int = 0
    low_competition_search_volume: int = 0
    market_score: float = 0.0
    picked_keyword: bool = False
    market_opportunity: bool = False


@dataclass(frozen=True)
class DeepDiveSeed:
    candidate: Candidate
    root_pin_url: str
    depth: int
    result: KeywordCandidateResult


def _deep_dive_max_depth(
    result: KeywordCandidateResult,
    *,
    style_max_depth: int,
    market_max_depth: int,
) -> int:
    if result.market_opportunity:
        return max(0, int(market_max_depth))
    if result.priority_score >= KEYWORD_QUOTE_HIGH_PRIORITY_SCORE:
        return max(0, int(style_max_depth))
    return 0


def _deep_dive_sort_key(seed: DeepDiveSeed) -> tuple[int, float, int, float]:
    return (
        1 if seed.result.market_opportunity else 0,
        seed.result.market_score,
        seed.result.max_search_volume,
        seed.result.priority_score,
    )


def _market_volume_score(search_volume: int) -> float:
    volume = max(0, int(search_volume))
    if volume >= 10_000:
        return 1.0
    if volume >= 1_000:
        return 0.85
    if volume >= 100:
        return 0.60
    if volume >= 10:
        return 0.35
    if volume > 0:
        return 0.20
    return 0.0


def _market_competition_score(value: Any) -> float:
    competition = str(value or "").strip().upper()
    return {
        "LOW": 1.0,
        "MEDIUM": 0.55,
        "UNKNOWN": 0.35,
        "UNSPECIFIED": 0.35,
        "HIGH": 0.15,
    }.get(competition, 0.30)


def _market_opportunity_score(
    items: list[dict[str, Any]],
    *,
    priority_score: float,
) -> tuple[float, bool]:
    best_score = 0.0
    picked = False
    for item in items:
        volume_score = _market_volume_score(int(item.get("search_volume") or 0))
        competition_score = _market_competition_score(item.get("competition"))
        item_picked = bool(item.get("picked"))
        picked = picked or item_picked
        score = (
            0.45 * volume_score
            + 0.25 * competition_score
            + 0.20 * max(0.0, min(1.0, float(priority_score)))
            + 0.10 * (1.0 if item_picked else 0.0)
        )
        best_score = max(best_score, score)
    return round(best_score, 4), picked


_KEYWORD_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
KEYWORD_MIN_WORDS = 2
_KEYWORD_HAT_METADATA_TERMS = (
    "hat",
    "cap",
    "trucker",
    "baseball cap",
    "dad hat",
    "snapback",
    "headwear",
)
_KEYWORD_NON_HAT_PRODUCT_TERMS = (
    "t-shirt",
    "tshirt",
    "tee shirt",
    "shirt",
    "hoodie",
    "sweatshirt",
    "sweater",
    "jacket",
    "dress",
    "skirt",
    "pants",
    "shorts",
    "onesie",
    "bodysuit",
    "mug",
    "tumbler",
    "cup",
    "water bottle",
    "bag",
    "tote",
    "backpack",
    "purse",
    "poster",
    "wall art",
    "wall decor",
    "canvas print",
    "sticker",
    "decal",
    "magnet",
    "shoe",
    "sneaker",
    "slipper",
    "phone case",
    "pillow",
    "blanket",
    "sign",
    "ornament",
    "necklace",
    "bracelet",
    "earring",
)
_KEYWORD_DIGITAL_PRODUCT_TERMS = (
    "digital download",
    "instant download",
    "printable",
    "svg file",
    "png file",
    "clipart",
    "template",
    "cut file",
    "sublimation design",
)


def _keyword_metadata_text(candidate: Candidate) -> str:
    return " ".join(
        value
        for value in (
            str(candidate.alt_text or "").strip(),
            str(candidate.context_text or "").strip(),
        )
        if value
    ).casefold()


def _keyword_metadata_prefilter_reason(candidate: Candidate) -> str | None:
    text = _keyword_metadata_text(candidate)
    if not text:
        return None
    has_hat = any(term in text for term in _KEYWORD_HAT_METADATA_TERMS)
    if has_hat:
        return None
    for term in _KEYWORD_NON_HAT_PRODUCT_TERMS:
        if term in text:
            return "explicit_non_hat_product:" + term
    for term in _KEYWORD_DIGITAL_PRODUCT_TERMS:
        if term in text:
            return "explicit_digital_product:" + term
    return None


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


def _partition_volume_result(
    keywords: list[str], response: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Keep missing or provider-pending volumes queued instead of marking them seen."""
    rows = {
        str(item.get("keyword") or "").strip().casefold(): item
        for item in (response.get("items") or [])
        if isinstance(item, dict)
    }
    resolved: list[str] = []
    pending: list[str] = []
    for quote in _dedupe(keywords):
        item = rows.get(quote.casefold())
        if item is None or str(item.get("provider") or "").casefold() == "pending":
            pending.append(quote)
        else:
            resolved.append(quote)
    return resolved, pending


def _quote_extract_status_is_terminal(status: int, error_code: str | None = None) -> bool:
    # Gemini/provider failures can be mapped to HTTP 422 by the API. Never
    # permanently discard a Pinterest Pin due to an upstream AI outage.
    if int(status) == 422:
        return error_code in {
            "quote_scout_image_base64_invalid",
            "quote_scout_image_type_rejected",
            "quote_scout_image_empty",
        }
    return int(status) in {400, 413}


def _quote_extract_error_code(exc: httpx.HTTPStatusError) -> str | None:
    try:
        payload = exc.response.json()
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return None
    return str(detail.get("code") or "").strip() or None


async def _browser_fetch_image(
    page: Any,
    image_url: str,
) -> tuple[bytes, str]:
    response = await page.context.request.get(
        image_url,
        timeout=20_000,
        fail_on_status_code=False,
    )
    if not response.ok:
        raise RuntimeError(
            "Browser image fetch returned HTTP " + str(response.status)
        )
    content_type = (
        str(response.headers.get("content-type") or "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise RuntimeError("Browser image fetch returned unsupported content type")
    content = await response.body()
    if not content:
        raise RuntimeError("Browser image fetch returned no data")
    if len(content) > BROWSER_FALLBACK_MAX_IMAGE_BYTES:
        raise RuntimeError("Browser image fetch exceeded size limit")
    return content, content_type


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
            "version": 3,
            "seen_pins": [],
            "seen_assets": [],
            "expanded_pins": [],
            "deep_expanded_pins": [],
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
    def deep_expanded_pins(self) -> set[str]:
        return {
            pin_history_key(value)
            for value in self._set("deep_expanded_pins")
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
        self.data["version"] = 3
        self.save()

    def remember_deep_expanded_pin(self, pin_url: str) -> None:
        expanded = list(self._set("deep_expanded_pins"))
        pin = pin_history_key(pin_url)
        if pin and pin not in expanded:
            expanded.append(pin)
        self.data["deep_expanded_pins"] = expanded[-50_000:]
        self.data["version"] = 3
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


class KeywordScoutCapacityPaused(Exception):
    """Server is protecting the shared Gemini pool; resume on the next cycle."""

    def __init__(self, retry_seconds: int = 180):
        self.retry_seconds = max(30, min(900, int(retry_seconds)))
        super().__init__("Shared Gemini analysis backlog")


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

    async def ensure_analysis_capacity(self) -> None:
        """Avoid expensive Pinterest detail work when CAM already reports AI pressure."""
        started = time.monotonic()
        path = (
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/diagnostics"
        )
        try:
            response = await self.client.get(
                path,
                headers={"X-Scout-Version": CLIENT_VERSION},
                timeout=httpx.Timeout(12.0, connect=5.0),
            )
            if response.status_code in {401, 403}:
                response.raise_for_status()
            if response.status_code >= 400:
                scout_debug_event(
                    "keyword_scout_capacity_preflight_warning",
                    status_code=response.status_code,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    action="fail_open_to_quote_endpoint",
                )
                return
            payload = response.json()
        except httpx.HTTPStatusError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            scout_debug_event(
                "keyword_scout_capacity_preflight_warning",
                error_type=exc.__class__.__name__,
                duration_ms=round((time.monotonic() - started) * 1000),
                action="fail_open_to_quote_endpoint",
            )
            return

        pressure = (
            payload.get("analysis_backpressure")
            if isinstance(payload, dict)
            else None
        )
        # Keyword has a protected fair-share lane even when Review is
        # backlogged. Prefer the lane-specific status over the global Review
        # pressure signal, which must not pause Keyword indefinitely.
        quote_gate = payload.get("keyword_quote_backpressure") if isinstance(payload, dict) else None
        effective_pressure = quote_gate if isinstance(quote_gate, dict) else pressure
        active = isinstance(effective_pressure, dict) and effective_pressure.get("active") is True
        if active:
            pending_jobs = int(pressure.get("pending_jobs") or 0) if isinstance(pressure, dict) else 0
            oldest_wait_seconds = int(pressure.get("oldest_wait_seconds") or 0) if isinstance(pressure, dict) else 0
            retry_seconds = int(effective_pressure.get("retry_seconds") or KEYWORD_CAPACITY_PREFLIGHT_RETRY_SECONDS)
            scout_debug_event(
                "keyword_scout_capacity_preflight_paused",
                pending_jobs=pending_jobs,
                oldest_wait_seconds=oldest_wait_seconds,
                retry_seconds=retry_seconds,
                reason=effective_pressure.get("reason"),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise KeywordScoutCapacityPaused(retry_seconds)

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
                if response.status_code == 503 and operation.startswith("extract_hat_quote"):
                    try:
                        error_detail = response.json().get("detail")
                    except (TypeError, ValueError, AttributeError):
                        error_detail = None
                    if (
                        isinstance(error_detail, dict)
                        and error_detail.get("code") in {"rrugc_analysis_backpressure", "rrugc_keyword_fair_share_wait"}
                    ):
                        try:
                            wait_seconds = int(response.headers.get("Retry-After", "180"))
                        except ValueError:
                            wait_seconds = 180
                        raise KeywordScoutCapacityPaused(wait_seconds)
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

    async def fetch_feedback(self) -> dict[str, set[str]]:
        path = f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/keyword-analysis/feedback"
        response = await self.client.get(path, timeout=httpx.Timeout(15.0, connect=5.0))
        response.raise_for_status()
        payload = response.json()
        return {
            "blocked_keywords": {str(x).casefold() for x in payload.get("blocked_keywords", [])},
            "blocked_pins": {pin_history_key(str(x)) for x in payload.get("blocked_pins", [])},
        }

    async def claim_feedback_task(self) -> dict[str, Any] | None:
        payload = await self._post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/keyword-analysis/priority/claim",
            {}, operation="claim_keyword_priority",
        )
        task = payload.get("task")
        return task if isinstance(task, dict) else None

    async def finish_feedback_task(self, task_id: str, lease_token: str, success: bool) -> None:
        await self._post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/keyword-analysis/priority/complete",
            {"id": task_id, "lease_token": lease_token, "success": success}, operation="finish_keyword_priority",
        )

    async def submit_cycle_metrics(self, cycle_id: str, *, scanned_pins: int,
                                   found_quotes: int, new_keywords: int,
                                   duplicate_pins: int) -> None:
        await self._post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/metrics",
            {"mode": "keyword", "cycle_id": cycle_id,
             "machine_label": socket.gethostname()[:160],
             "scanned_pins": scanned_pins, "found_quotes": found_quotes,
             "new_keywords": new_keywords, "duplicate_pins": duplicate_pins},
            operation="record_keyword_cycle_metrics",
        )

    async def extract_quote(
        self,
        candidate,
        *,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pin_url": candidate.pin_url,
            "image_url": candidate.image_url,
            "alt_text": candidate.alt_text,
        }
        operation = "extract_hat_quote"
        if image_bytes is not None:
            payload["image_base64"] = base64.b64encode(image_bytes).decode("ascii")
            payload["image_mime_type"] = image_mime_type
            operation = "extract_hat_quote_browser_fallback"
        return await self._post(
            f"/api/v1/realistic-review-ugc/scout-agents/{self.agent_id}/quote-analysis/extract",
            payload,
            operation=operation,
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
    blocked_keywords: set[str] | None = None,
) -> int:
    pending = [q for q in history.pending_quotes if q.casefold() not in (blocked_keywords or set())]
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
        resolved, still_pending = _partition_volume_result(chunk, result)
        if resolved:
            history.remember_quotes(resolved)
        saved += len(resolved)
        scout_debug_event(
            "keyword_scout_pending_volume_saved",
            count=len(resolved),
            remaining=len(still_pending),
            provider_requested=int(result.get("provider_requested") or 0),
            cached=int(result.get("cached") or 0),
        )
        print("Recovered pending keyword volumes: " + str(len(resolved)))
    return saved


async def _process_keyword_candidate(
    client: QuoteScoutClient,
    history: KeywordScoutHistory,
    candidate: Candidate,
    *,
    source: str,
    root_pin_url: str,
    deep_dive_min_search_volume: int = DEFAULT_DEEP_DIVE_MIN_SEARCH_VOLUME,
    browser_page: Any | None = None,
    blocked_keywords: set[str] | None = None,
) -> KeywordCandidateResult:
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
        error_code = _quote_extract_error_code(exc)
        fallback_used = False
        if (
            status == 502
            and error_code == "quote_scout_image_fetch_failed"
            and browser_page is not None
        ):
            try:
                image_bytes, image_mime_type = await _browser_fetch_image(
                    browser_page,
                    candidate.image_url,
                )
                scout_debug_event(
                    "keyword_scout_browser_image_fallback",
                    source=source,
                    root_pin_url=root_pin_url,
                    pin_url=candidate.pin_url,
                    image_url=candidate.image_url,
                    size_bytes=len(image_bytes),
                    image_mime_type=image_mime_type,
                )
                result = await client.extract_quote(
                    candidate,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                )
                fallback_used = True
            except Exception as fallback_exc:
                scout_debug_event(
                    "keyword_scout_browser_image_fallback_failed",
                    source=source,
                    root_pin_url=root_pin_url,
                    pin_url=candidate.pin_url,
                    image_url=candidate.image_url,
                    error_type=fallback_exc.__class__.__name__,
                    error=str(fallback_exc)[:500],
                )
        if not fallback_used:
            if status == 422 and error_code in {"gemini_invalid_json", "gemini_invalid_document", "quote_scout_provider_payload_invalid"}:
                scout_debug_event(
                    "keyword_scout_provider_error_paused",
                    source=source,
                    error_code=error_code,
                    status_code=status,
                    retry_seconds=KEYWORD_CAPACITY_PREFLIGHT_RETRY_SECONDS,
                )
                raise KeywordScoutCapacityPaused(KEYWORD_CAPACITY_PREFLIGHT_RETRY_SECONDS)
            if _quote_extract_status_is_terminal(status, error_code):
                history.remember_candidate(candidate.pin_url, candidate.image_url)
            retryable = not _quote_extract_status_is_terminal(status, error_code)
            scout_debug_event(
                "keyword_scout_quote_extract_failed",
                source=source,
                root_pin_url=root_pin_url,
                pin_url=candidate.pin_url,
                image_url=candidate.image_url,
                status_code=status,
                error_code=error_code,
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
            return KeywordCandidateResult()
    except KeywordScoutCapacityPaused:
        # Do not mark the Pin as seen: this is capacity deferral, not a negative
        # vision result. Abort the current Pinterest scan until Gemini recovers.
        raise
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
        return KeywordCandidateResult()

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
        and quote.casefold() not in (blocked_keywords or set())
    ]
    priority_score = _quote_priority_score(result)
    priority_label = _quote_priority_label(priority_score)
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
        confidence=priority_score,
        priority_label=priority_label,
        priority_min_score=KEYWORD_QUOTE_MIN_PRIORITY_SCORE,
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
        return KeywordCandidateResult(
            priority_score=priority_score,
            quotes=tuple(quotes),
        )

    if priority_score < KEYWORD_QUOTE_MIN_PRIORITY_SCORE:
        scout_debug_event(
            "keyword_scout_quote_skipped",
            source=source,
            root_pin_url=root_pin_url,
            pin_url=candidate.pin_url,
            image_url=candidate.image_url,
            reason="low_quote_clarity",
            quotes=new_quotes,
            priority_score=priority_score,
            priority_min_score=KEYWORD_QUOTE_MIN_PRIORITY_SCORE,
        )
        print(
            "quote_priority=low skip_volume=yes score="
            + f"{priority_score:.2f}"
            + " minimum="
            + f"{KEYWORD_QUOTE_MIN_PRIORITY_SCORE:.2f}"
            + " quote="
            + " | ".join(new_quotes)
        )
        return KeywordCandidateResult(
            priority_score=priority_score,
            quotes=tuple(new_quotes),
        )

    print(
        "quote_priority="
        + priority_label
        + " score="
        + f"{priority_score:.2f}"
        + " quote="
        + " | ".join(new_quotes)
    )

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
        return KeywordCandidateResult(
            quote_delta=1,
            priority_score=priority_score,
            quotes=tuple(new_quotes),
        )

    verified_quotes, pending_quotes = _partition_volume_result(
        new_quotes, volume,
    )
    if verified_quotes:
        history.remember_quotes(verified_quotes)
    if pending_quotes:
        history.remember_pending_quotes(pending_quotes)
        scout_debug_event(
            "keyword_scout_partial_volume_deferred",
            source=source,
            pin_url=candidate.pin_url,
            pending_quotes=pending_quotes,
            pending_count=len(pending_quotes),
        )
    items = [
        item
        for item in (volume.get("items") or [])
        if isinstance(item, dict)
        and str(item.get("keyword") or "").casefold() in {
            quote.casefold() for quote in verified_quotes
        }
    ]
    volume_by_keyword = {
        str(item.get("keyword") or "").casefold(): int(
            item.get("search_volume") or 0
        )
        for item in items
    }
    max_search_volume = max(
        (int(item.get("search_volume") or 0) for item in items),
        default=0,
    )
    low_competition_search_volume = max(
        (
            int(item.get("search_volume") or 0)
            for item in items
            if str(item.get("competition") or "").strip().upper() == "LOW"
        ),
        default=0,
    )
    market_score, picked_keyword = _market_opportunity_score(
        items,
        priority_score=priority_score,
    )
    # Keep the old threshold as a strong positive signal, but do not require it.
    # Real production data often reports HIGH/UNKNOWN competition even for
    # promising quote phrases, while picked phrases and very high volume should
    # still earn deeper Pinterest exploration.
    hard_market_match = (
        low_competition_search_volume >= max(
            0,
            int(deep_dive_min_search_volume),
        )
    )
    market_opportunity = (
        hard_market_match
        or market_score >= MARKET_DEEP_DIVE_SCORE_THRESHOLD
    )
    for quote in verified_quotes:
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
        quote_count=len(verified_quotes),
        pending_count=len(pending_quotes),
        quotes=verified_quotes,
        volumes=[
            {
                "keyword": str(item.get("keyword") or ""),
                "search_volume": int(item.get("search_volume") or 0),
                "competition": str(item.get("competition") or ""),
            }
            for item in items
        ],
        max_search_volume=max_search_volume,
        low_competition_search_volume=low_competition_search_volume,
        market_score=market_score,
        picked_keyword=picked_keyword,
        deep_dive_min_search_volume=int(deep_dive_min_search_volume),
        market_opportunity=market_opportunity,
        provider_requested=int(volume.get("provider_requested") or 0),
        cached=int(volume.get("cached") or 0),
    )
    if market_opportunity:
        print(
            "deep_dive_market=yes score="
            + str(market_score)
            + " max_volume="
            + str(max_search_volume)
            + "/mo low_comp_volume="
            + str(low_competition_search_volume)
            + "/mo picked="
            + str(picked_keyword).lower()
            + " pin="
            + candidate.pin_url
        )
    return KeywordCandidateResult(
        quote_delta=1,
        saved_delta=len(verified_quotes),
        priority_score=priority_score,
        quotes=tuple(new_quotes),
        max_search_volume=max_search_volume,
        low_competition_search_volume=low_competition_search_volume,
        market_score=market_score,
        picked_keyword=picked_keyword,
        market_opportunity=market_opportunity,
    )


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

    print("Scout version         : " + CLIENT_VERSION)
    print("Stage 0 Keyword Scout log: " + str(log_path))
    print("Remote Scout log      : API enabled · retention 5 days")
    print("Pinterest query       : " + args.seed_query)
    print("Pinterest profile     : " + str(profile_dir))
    print("Related per Pin       : " + str(args.related_per_pin))
    print(
        "Deep-dive per detail  : "
        + str(args.deep_dive_related_per_pin)
        + " Pins"
    )
    print(
        "Deep-dive budget      : "
        + str(args.deep_dive_seeds_per_cycle)
        + " prioritized details/cycle"
    )
    print(
        "Deep-dive market      : adaptive score >= "
        + str(MARKET_DEEP_DIVE_SCORE_THRESHOLD)
        + " (volume + competition + visual clarity + Pick)"
    )
    print(
        "Deep-dive depth       : style="
        + str(args.deep_dive_style_max_depth)
        + " market="
        + str(args.deep_dive_market_max_depth)
    )
    print("Mode                  : adaptive recursive quote discovery + Google Ads volume")
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
        deep_dive_related_per_pin=args.deep_dive_related_per_pin,
        deep_dive_seeds_per_cycle=args.deep_dive_seeds_per_cycle,
        deep_dive_min_search_volume=args.deep_dive_min_search_volume,
        deep_dive_style_max_depth=args.deep_dive_style_max_depth,
        deep_dive_market_max_depth=args.deep_dive_market_max_depth,
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
    runtime_error_streak = 0
    successful_cycles_since_restart = 0
    no_quote_cycles = 0

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
            "Complete Pinterest sign-in there using Pinterest email/password. "
            "Do not use Continue with Google for the dedicated Scout profile if Google "
            "shows 'This browser or app may not be secure'. Close the Chrome window after "
            "Pinterest is fully signed in; Keyword Scout will verify the session and resume."
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

    async def ensure_keyword_startup_login() -> None:
        if not pinterest_login_ready(profile_dir):
            scout_debug_event(
                "startup_saved_session_probe",
                scout_type="keyword",
                profile_dir=str(profile_dir),
            )
            print(
                "Pinterest login marker is missing. Checking the saved Keyword profile "
                "session before asking for manual sign-in..."
            )

        while True:
            if page is None:
                await open_browser_runtime()
            gate = await startup_access_gate(page)
            if gate is None:
                mark_pinterest_login_ready(profile_dir)
                scout_debug_event("startup_access_ready", scout_type="keyword")
                print("Pinterest login verified. Keyword Scout can start.")
                return
            clear_pinterest_login_ready(profile_dir)
            scout_debug_event(
                "startup_access_blocked",
                scout_type="keyword",
                gate=gate,
            )
            print(
                "Pinterest is not ready for Keyword Scout yet ("
                + gate
                + "). No search cycle will start before login/verification is complete."
            )
            await bootstrap_keyword_login()

    try:
        await ensure_keyword_startup_login()

        while True:
            cycle_id = str(uuid4())
            priority_task: dict[str, Any] | None = None
            try:
                await client.ensure_analysis_capacity()
                directives = await client.fetch_feedback()
                blocked_keywords = directives["blocked_keywords"]
                blocked_pins = directives["blocked_pins"]
                await _flush_pending_quote_volumes(client, history, blocked_keywords)
                priority_task = await client.claim_feedback_task()
                current_query = str(priority_task.get("keyword") or args.seed_query) if priority_task else args.seed_query
                priority_pin = (
                    Candidate(pin_url=priority_task["pin_url"], image_url=priority_task["image_url"])
                    if priority_task and priority_task.get("type") == "pin"
                    and priority_task.get("pin_url") and priority_task.get("image_url")
                    else None
                )
                priority_pin_key = pin_history_key(priority_pin.pin_url) if priority_pin else None
                search_url = (
                    "https://www.pinterest.com/search/pins/?q="
                    + quote_plus(current_query)
                )
                if page is None:
                    await open_browser_runtime()
                # Pinterest often holds DOMContentLoaded behind third-party
                # requests for 60s even though navigation has committed.
                # Pin-growth and access checks below still validate readiness.
                response = await page.goto(
                    search_url,
                    wait_until="commit",
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
                duplicate_pins = 0
                extracted = 0
                saved_quotes = 0
                related_scanned = 0
                related_fresh = 0
                related_processed = 0
                deep_detail_expanded = 0
                deep_related_scanned = 0
                deep_processed = 0
                empty_batch_streak = 0
                cycle_seen: set[str] = set()
                for batch in range(args.max_scroll_batches):
                    await paced_wait(page, pace.inspect_dwell_ms)
                    visible = await extract_visible_pin_candidates(page)
                    if priority_pin is not None and batch == 0 and pin_history_key(priority_pin.pin_url) not in blocked_pins:
                        visible = [priority_pin, *visible]
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
                        if not pin_key or pin_key in cycle_seen or pin_key in blocked_pins:
                            continue
                        cycle_seen.add(pin_key)
                        needs_expansion = pin_key not in expanded_pins or pin_key == priority_pin_key
                        if pin_key in persistent_pins and not needs_expansion:
                            duplicate_pins += 1
                            continue
                        if (
                            asset_key
                            and asset_key in persistent_assets
                            and not needs_expansion
                        ):
                            duplicate_pins += 1
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
                            needs_expansion = pin_key not in history.expanded_pins or pin_key == priority_pin_key
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
                                if not related_pin_key or related_pin_key in cycle_seen or related_pin_key in blocked_pins:
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
                            if not expansion_complete:
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
                            deep_queue: list[DeepDiveSeed] = []
                            deep_queued_keys: set[str] = set()
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

                                metadata_skip = _keyword_metadata_prefilter_reason(row)
                                if metadata_skip is not None:
                                    history.remember_candidate(
                                        row.pin_url,
                                        row.image_url,
                                    )
                                    scout_debug_event(
                                        "keyword_scout_metadata_prefiltered",
                                        source=source,
                                        root_pin_url=root_pin_url,
                                        pin_url=row.pin_url,
                                        reason=metadata_skip,
                                    )
                                    continue

                                candidate_result = await _process_keyword_candidate(
                                    client,
                                    history,
                                    row,
                                    source=source,
                                    root_pin_url=root_pin_url,
                                    deep_dive_min_search_volume=(
                                        args.deep_dive_min_search_volume
                                    ),
                                    browser_page=detail_page or page,
                                    blocked_keywords=blocked_keywords,
                                )
                                extracted += candidate_result.quote_delta
                                saved_quotes += candidate_result.saved_delta
                                max_depth = _deep_dive_max_depth(
                                    candidate_result,
                                    style_max_depth=args.deep_dive_style_max_depth,
                                    market_max_depth=args.deep_dive_market_max_depth,
                                )
                                row_pin_key = pin_history_key(row.pin_url)
                                if (
                                    max_depth >= 1
                                    and row_pin_key
                                    and row_pin_key not in history.deep_expanded_pins
                                    and row_pin_key not in deep_queued_keys
                                ):
                                    deep_queued_keys.add(row_pin_key)
                                    deep_queue.append(
                                        DeepDiveSeed(
                                            candidate=row,
                                            root_pin_url=root_pin_url,
                                            depth=1,
                                            result=candidate_result,
                                        )
                                    )
                                    scout_debug_event(
                                        "keyword_scout_deep_dive_queued",
                                        pin_url=row.pin_url,
                                        root_pin_url=root_pin_url,
                                        depth=1,
                                        max_depth=max_depth,
                                        reason=(
                                            "market"
                                            if candidate_result.market_opportunity
                                            else "visual_style"
                                        ),
                                        priority_score=(
                                            candidate_result.priority_score
                                        ),
                                        max_search_volume=(
                                            candidate_result.max_search_volume
                                        ),
                                    )
                                await page.wait_for_timeout(
                                    random.randint(*pace.submit_pause_ms)
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

                            # Deep-dive has a reserved budget separate from root
                            # discovery. Previously root scanning could consume the
                            # complete max_pins_per_cycle budget, leaving every queued
                            # 150-image deep-dive seed unprocessed.
                            while (
                                deep_queue
                                and deep_processed < args.deep_dive_seeds_per_cycle
                            ):
                                deep_queue.sort(
                                    key=_deep_dive_sort_key,
                                    reverse=True,
                                )
                                seed = deep_queue.pop(0)
                                seed_key = pin_history_key(seed.candidate.pin_url)
                                seed_max_depth = _deep_dive_max_depth(
                                    seed.result,
                                    style_max_depth=args.deep_dive_style_max_depth,
                                    market_max_depth=args.deep_dive_market_max_depth,
                                )
                                if (
                                    not seed_key
                                    or seed.depth > seed_max_depth
                                    or seed_key in history.deep_expanded_pins
                                ):
                                    continue

                                deep_processed += 1
                                deep_rows: list[Candidate] = []
                                try:
                                    deep_rows = await extract_related_candidates(
                                        detail_page or page,
                                        seed.candidate,
                                        pace=pace,
                                        limit=args.deep_dive_related_per_pin,
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
                                        "keyword_scout_deep_dive_scan_failed",
                                        pin_url=seed.candidate.pin_url,
                                        root_pin_url=seed.root_pin_url,
                                        depth=seed.depth,
                                        error_type=exc.__class__.__name__,
                                        error=str(exc)[:500],
                                    )
                                    print(
                                        "deep_dive_scan_failed pin="
                                        + seed.candidate.pin_url
                                        + " depth="
                                        + str(seed.depth)
                                        + " error="
                                        + exc.__class__.__name__
                                    )

                                deep_related_scanned += len(deep_rows)
                                deep_fresh: list[Candidate] = []
                                persistent_pins = history.seen_pins
                                persistent_assets = history.seen_assets
                                for deep_row in deep_rows:
                                    deep_pin_key = pin_history_key(
                                        deep_row.pin_url
                                    )
                                    deep_asset_key = pinimg_asset_key(
                                        deep_row.image_url
                                    )
                                    if (
                                        not deep_pin_key
                                        or deep_pin_key in cycle_seen
                                        or deep_pin_key in blocked_pins
                                        or deep_pin_key in persistent_pins
                                    ):
                                        continue
                                    if (
                                        deep_asset_key
                                        and deep_asset_key in persistent_assets
                                    ):
                                        continue
                                    cycle_seen.add(deep_pin_key)
                                    deep_fresh.append(deep_row)

                                related_fresh += len(deep_fresh)
                                resolved_deep: list[Candidate] = []
                                if deep_fresh:
                                    resolved_deep = await resolve_pin_details(
                                        page,
                                        deep_fresh,
                                        detail_page=detail_page,
                                        concurrency=1,
                                        fallback_on_error=False,
                                    )
                                valid_deep = [
                                    row
                                    for row in resolved_deep
                                    if allowed_image(row.image_url)
                                ]
                                deep_complete = (
                                    args.deep_dive_related_per_pin <= 0
                                    or len(deep_rows) > 0
                                )
                                scout_debug_event(
                                    "keyword_scout_deep_dive_scanned",
                                    pin_url=seed.candidate.pin_url,
                                    root_pin_url=seed.root_pin_url,
                                    depth=seed.depth,
                                    max_depth=seed_max_depth,
                                    reason=(
                                        "market"
                                        if seed.result.market_opportunity
                                        else "visual_style"
                                    ),
                                    requested=args.deep_dive_related_per_pin,
                                    scanned=len(deep_rows),
                                    fresh=len(deep_fresh),
                                    resolved=len(valid_deep),
                                    complete=deep_complete,
                                )
                                print(
                                    "deep_dive pin="
                                    + seed.candidate.pin_url
                                    + " depth="
                                    + str(seed.depth)
                                    + "/"
                                    + str(seed_max_depth)
                                    + " related_scanned="
                                    + str(len(deep_rows))
                                    + "/"
                                    + str(args.deep_dive_related_per_pin)
                                    + " fresh="
                                    + str(len(deep_fresh))
                                    + " reason="
                                    + (
                                        "market"
                                        if seed.result.market_opportunity
                                        else "visual_style"
                                    )
                                )

                                for child in valid_deep:
                                    child_key = pin_history_key(child.pin_url)
                                    child_asset_key = pinimg_asset_key(
                                        child.image_url
                                    )
                                    if child_key in history.seen_pins:
                                        continue
                                    if (
                                        child_asset_key
                                        and child_asset_key in history.seen_assets
                                    ):
                                        continue
                                    related_processed += 1
                                    metadata_skip = _keyword_metadata_prefilter_reason(
                                        child
                                    )
                                    if metadata_skip is not None:
                                        history.remember_candidate(
                                            child.pin_url,
                                            child.image_url,
                                        )
                                        scout_debug_event(
                                            "keyword_scout_metadata_prefiltered",
                                            source="deep_related",
                                            root_pin_url=seed.root_pin_url,
                                            pin_url=child.pin_url,
                                            reason=metadata_skip,
                                        )
                                        continue
                                    child_result = (
                                        await _process_keyword_candidate(
                                            client,
                                            history,
                                            child,
                                            source="deep_related",
                                            root_pin_url=seed.root_pin_url,
                                            deep_dive_min_search_volume=(
                                                args.deep_dive_min_search_volume
                                            ),
                                            browser_page=detail_page or page,
                                            blocked_keywords=blocked_keywords,
                                        )
                                    )
                                    extracted += child_result.quote_delta
                                    saved_quotes += child_result.saved_delta

                                    child_depth = seed.depth + 1
                                    child_max_depth = _deep_dive_max_depth(
                                        child_result,
                                        style_max_depth=(
                                            args.deep_dive_style_max_depth
                                        ),
                                        market_max_depth=(
                                            args.deep_dive_market_max_depth
                                        ),
                                    )
                                    if (
                                        child_key
                                        and child_depth <= child_max_depth
                                        and child_key
                                        not in history.deep_expanded_pins
                                        and child_key not in deep_queued_keys
                                    ):
                                        deep_queued_keys.add(child_key)
                                        deep_queue.append(
                                            DeepDiveSeed(
                                                candidate=child,
                                                root_pin_url=seed.root_pin_url,
                                                depth=child_depth,
                                                result=child_result,
                                            )
                                        )
                                        scout_debug_event(
                                            "keyword_scout_deep_dive_queued",
                                            pin_url=child.pin_url,
                                            root_pin_url=seed.root_pin_url,
                                            depth=child_depth,
                                            max_depth=child_max_depth,
                                            reason=(
                                                "market"
                                                if child_result.market_opportunity
                                                else "visual_style"
                                            ),
                                            priority_score=(
                                                child_result.priority_score
                                            ),
                                            max_search_volume=(
                                                child_result.max_search_volume
                                            ),
                                        )
                                    await page.wait_for_timeout(
                                        random.randint(*pace.submit_pause_ms)
                                    )

                                if deep_complete:
                                    history.remember_deep_expanded_pin(
                                        seed.candidate.pin_url
                                    )
                                    deep_detail_expanded += 1
                                    scout_debug_event(
                                        "keyword_scout_deep_dive_completed",
                                        pin_url=seed.candidate.pin_url,
                                        root_pin_url=seed.root_pin_url,
                                        depth=seed.depth,
                                        resolved=len(valid_deep),
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
                        deep_detail_expanded=deep_detail_expanded,
                        deep_related_scanned=deep_related_scanned,
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
                        + " deep_details="
                        + str(deep_detail_expanded)
                        + " deep_scanned="
                        + str(deep_related_scanned)
                        + " quote_pins="
                        + str(extracted)
                        + " saved_quotes="
                        + str(saved_quotes)
                    )
                    if processed >= args.max_pins_per_cycle:
                        break
                    await _scroll_search_page(page, pace)

                runtime_error_streak = 0
                successful_cycles_since_restart += 1
                no_quote_cycles = no_quote_cycles + 1 if saved_quotes == 0 else 0
                if no_quote_cycles >= 3 and no_quote_cycles % 3 == 0:
                    scout_debug_event(
                        "keyword_scout_no_progress_warning",
                        consecutive_cycles=no_quote_cycles,
                        last_cycle_processed=processed,
                        last_cycle_quote_pins=extracted,
                        last_cycle_saved_quotes=saved_quotes,
                        action="cooldown_to_protect_pinterest_access",
                    )
                    print(
                        "Keyword Scout has found no new saved quotes for "
                        + str(no_quote_cycles)
                        + " cycles; reducing Pinterest request frequency."
                    )
                scout_debug_event(
                    "keyword_scout_cycle_completed",
                    query=args.seed_query,
                    processed=processed,
                    related_scanned=related_scanned,
                    related_fresh=related_fresh,
                    related_processed=related_processed,
                    deep_detail_expanded=deep_detail_expanded,
                    deep_related_scanned=deep_related_scanned,
                    quote_pins=extracted,
                    saved_quotes=saved_quotes,
                    cycle_seconds=args.cycle_seconds,
                )
                try:
                    await client.submit_cycle_metrics(
                        cycle_id, scanned_pins=processed + related_processed,
                        found_quotes=extracted, new_keywords=saved_quotes,
                        duplicate_pins=duplicate_pins,
                    )
                    if priority_task:
                        await client.finish_feedback_task(
                            str(priority_task["id"]), str(priority_task["lease_token"]), success=processed > 0,
                        )
                except Exception as exc:
                    scout_debug_event(
                        "keyword_scout_feedback_sync_failed",
                        error_type=exc.__class__.__name__, error=str(exc)[:300],
                    )
                if args.once:
                    return
                next_cycle_seconds = (
                    args.cycle_seconds
                    if processed > 0
                    else min(args.cycle_seconds, EMPTY_CYCLE_RETRY_SECONDS)
                )
                if no_quote_cycles >= 3:
                    next_cycle_seconds = max(next_cycle_seconds, 600)
                print(
                    "Keyword Scout cycle complete. "
                    + "processed="
                    + str(processed)
                    + " saved_quotes="
                    + str(saved_quotes)
                    + " deep_details="
                    + str(deep_detail_expanded)
                    + " deep_scanned="
                    + str(deep_related_scanned)
                    + " next_cycle_in="
                    + str(next_cycle_seconds)
                    + "s"
                    + (" (empty-search fast retry)" if processed == 0 else "")
                )
                await asyncio.sleep(next_cycle_seconds)
            except KeywordScoutCapacityPaused as exc:
                scout_debug_event(
                    "keyword_scout_gemini_backpressure_paused",
                    retry_seconds=exc.retry_seconds,
                )
                print(
                    "Gemini shared analysis backlog is high. Keyword Scout "
                    + "will retry in "
                    + str(exc.retry_seconds)
                    + " seconds without marking the Pin as processed."
                )
                if args.once:
                    raise
                await asyncio.sleep(exc.retry_seconds)
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
                runtime_error_streak += 1
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
                    runtime_error_streak=runtime_error_streak,
                    retry_seconds=retry_seconds,
                )
                if runtime_error_streak >= SCOUT_RUNTIME_ERRORS_BEFORE_RESTART:
                    scout_debug_event(
                        "scout_restart_requested",
                        scout_type="keyword",
                        error_code="keyword_scout_runtime_error_threshold",
                        runtime_error_streak=runtime_error_streak,
                        last_error_type=exc.__class__.__name__,
                        healthy_progress=successful_cycles_since_restart > 0,
                    )
                    print(
                        "Keyword Scout hit "
                        + str(runtime_error_streak)
                        + " unexpected runtime errors; restarting the Scout runtime."
                    )
                    raise ScoutRestartRequested(
                        "keyword_scout_runtime_error_threshold",
                        healthy_progress=successful_cycles_since_restart > 0,
                        last_error_type=exc.__class__.__name__,
                    ) from exc
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


async def supervise_pinterest_quote_scout(args: argparse.Namespace) -> None:
    restart_attempts = 0
    machine_label = (
        os.getenv("RRUGC_MACHINE_LABEL")
        or os.getenv("COMPUTERNAME")
        or os.getenv("HOSTNAME")
        or "keyword-scout"
    )
    while True:
        try:
            await run_pinterest_quote_scout(args)
            return
        except ScoutRestartRequested as exc:
            if exc.healthy_progress:
                restart_attempts = 0
            if restart_attempts >= SCOUT_MAX_AUTOMATIC_RESTARTS:
                fatal_code = "keyword_scout_restart_limit_exceeded"
                scout_debug_event(
                    "scout_fatal_stop",
                    scout_type="keyword",
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
                    "Keyword Scout stopped after "
                    + str(SCOUT_MAX_AUTOMATIC_RESTARTS)
                    + " automatic restarts without healthy progress."
                ) from exc

            restart_attempts += 1
            delay = scout_restart_delay_seconds(restart_attempts)
            scout_debug_event(
                "scout_automatic_restart",
                scout_type="keyword",
                restart_attempt=restart_attempts,
                restart_limit=SCOUT_MAX_AUTOMATIC_RESTARTS,
                delay_seconds=delay,
                trigger=exc.error_code,
                last_error_type=exc.last_error_type,
            )
            print(
                "Restarting Keyword Scout "
                + str(restart_attempts)
                + "/"
                + str(SCOUT_MAX_AUTOMATIC_RESTARTS)
                + " in "
                + str(delay)
                + "s after repeated errors."
            )
            await asyncio.sleep(delay)


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
        help="Scan up to this many related Pins from each normal root detail page.",
    )
    parser.add_argument(
        "--deep-dive-related-per-pin",
        type=int,
        default=DEFAULT_DEEP_DIVE_RELATED_PER_PIN,
        help="Scan up to this many related Pins from each prioritized deep-dive detail page.",
    )
    parser.add_argument(
        "--deep-dive-seeds-per-cycle",
        type=int,
        default=DEFAULT_DEEP_DIVE_SEEDS_PER_CYCLE,
        help="Reserved prioritized detail expansions per cycle, independent of the root Pin budget.",
    )
    parser.add_argument(
        "--deep-dive-min-search-volume",
        type=int,
        default=DEFAULT_DEEP_DIVE_MIN_SEARCH_VOLUME,
        help="Minimum monthly search volume for Competition LOW market deep-dive priority.",
    )
    parser.add_argument(
        "--deep-dive-style-max-depth",
        type=int,
        default=DEFAULT_DEEP_DIVE_STYLE_MAX_DEPTH,
        help="Maximum recursive detail depth for visually preferred clear-quote Pins.",
    )
    parser.add_argument(
        "--deep-dive-market-max-depth",
        type=int,
        default=DEFAULT_DEEP_DIVE_MARKET_MAX_DEPTH,
        help="Maximum recursive detail depth for high-volume Competition LOW quotes.",
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
        if (
            args.deep_dive_related_per_pin < 1
            or args.deep_dive_related_per_pin > PIN_RELATED_HARD_LIMIT
        ):
            parser.error(
                "--deep-dive-related-per-pin must be between 1 and "
                + str(PIN_RELATED_HARD_LIMIT)
            )
        if args.deep_dive_seeds_per_cycle < 1 or args.deep_dive_seeds_per_cycle > 12:
            parser.error("--deep-dive-seeds-per-cycle must be between 1 and 12")
        if args.deep_dive_min_search_volume < 0:
            parser.error("--deep-dive-min-search-volume must be at least 0")
        if (
            args.deep_dive_style_max_depth < 1
            or args.deep_dive_style_max_depth > 6
        ):
            parser.error("--deep-dive-style-max-depth must be between 1 and 6")
        if (
            args.deep_dive_market_max_depth < args.deep_dive_style_max_depth
            or args.deep_dive_market_max_depth > 8
        ):
            parser.error(
                "--deep-dive-market-max-depth must be >= style depth and <= 8"
            )
        try:
            asyncio.run(supervise_pinterest_quote_scout(args))
        except ScoutFatalStop as exc:
            print(str(exc), file=sys.stderr)
            return SCOUT_FATAL_EXIT_CODE
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
