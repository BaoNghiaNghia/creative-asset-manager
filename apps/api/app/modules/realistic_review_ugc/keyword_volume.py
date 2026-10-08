from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
import json
from datetime import datetime, timedelta, timezone
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel


_LOGGER = logging.getLogger(__name__)

KEYWORD_VOLUME_URL = "https://aebrowse.com/mcp-google-ads/api_keyword_volume.php"
KEYWORD_VOLUME_PROVIDER = "aebrowse_google_ads"
KEYWORD_VOLUME_PENDING_PROVIDER = "pending"
KEYWORD_VOLUME_CACHE_TTL = timedelta(hours=24)
KEYWORD_VOLUME_MAX_BATCH = 50
KEYWORD_VOLUME_MAX_LENGTH = 500
KEYWORD_VOLUME_MIN_WORDS = 2
KEYWORD_VOLUME_RETRIES = 2
KEYWORD_TRADEMARK_SOURCE = "aebrowse_google_ads"
KEYWORD_TRADEMARK_STATUS_MAP = {
    "SAFE": "safe",
    "WARNING": "warning",
    "DANGER": "danger",
}


def trademark_evidence_is_current(row: RrugcKeywordVolumeModel) -> bool:
    """Only unmodified keyword screening counts; legacy '+ hat' is stale."""
    raw = row.provider_raw_json
    if not isinstance(raw, dict):
        return False
    recorded = raw.get("_trademark_screened_keyword")
    tm = raw.get("trademark")
    return (
        isinstance(recorded, str)
        and recorded.strip().casefold() == row.keyword.strip().casefold()
        and isinstance(tm, dict)
        and KEYWORD_TRADEMARK_STATUS_MAP.get(
            str(tm.get("status") or "").strip().upper()
        ) == row.trademark_status
        and row.trademark_source == KEYWORD_TRADEMARK_SOURCE
    )


def apply_provider_trademark(
    row: RrugcKeywordVolumeModel,
    item: dict,
    *,
    checked_at: datetime,
) -> bool:
    """Persist per-keyword AEBrowse screening, never its aggregate summary.

    An absent/malformed TM object preserves previously verified evidence.
    Provider SAFE is only a screening result, not a legal clearance.
    """
    # The screened phrase MUST exactly match the stored original keyword.
    if str(item.get("keyword") or "").strip().casefold() != row.keyword.strip().casefold():
        return False
    tm = item.get("trademark")
    if not isinstance(tm, dict):
        return False
    status = KEYWORD_TRADEMARK_STATUS_MAP.get(
        str(tm.get("status") or "").strip().upper()
    )
    if not status:
        return False
    row.trademark_status = status
    row.trademark_checked_at = checked_at
    row.trademark_source = KEYWORD_TRADEMARK_SOURCE
    count = _as_int(tm.get("conflict_count"), default=-1)
    row.trademark_match_count = count if count >= 0 else None
    raw = dict(row.provider_raw_json or {})
    raw["trademark"] = tm
    raw["_trademark_screened_keyword"] = row.keyword
    row.provider_raw_json = raw
    return True


def restore_cached_provider_trademark(row: RrugcKeywordVolumeModel) -> bool:
    """Upgrade stored responses without an unnecessary provider API call."""
    if trademark_evidence_is_current(row):
        return False
    raw = row.provider_raw_json
    if not isinstance(raw, dict):
        return False
    recorded = raw.get("_trademark_screened_keyword")
    if not isinstance(recorded, str) or recorded.strip().casefold() != row.keyword.strip().casefold():
        return False
    return apply_provider_trademark(
        row, dict(raw, keyword=recorded),
        checked_at=_aware(row.trademark_checked_at) or datetime.now(timezone.utc),
    )


class KeywordVolumeError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class KeywordVolumeResolveResult:
    rows: list[RrugcKeywordVolumeModel]
    requested: int
    provider_requested: int
    cached: int


def _extract_keyword_text(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("phrase") or ""
    text = str(value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        parsed: object = None
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


_KEYWORD_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _keyword_word_count(value: str) -> int:
    return len(_KEYWORD_WORD_RE.findall(value))


def normalize_keyword(value: str) -> tuple[str, str]:
    clean = re.sub(r"\s+", " ", _extract_keyword_text(value)).strip(
        " \t\r\n\"'“”"
    )
    if not clean:
        raise KeywordVolumeError(
            "rrugc_keyword_required",
            "Keyword cannot be empty.",
        )
    if _keyword_word_count(clean) < KEYWORD_VOLUME_MIN_WORDS:
        raise KeywordVolumeError(
            "rrugc_keyword_too_short",
            f"Keyword must contain at least {KEYWORD_VOLUME_MIN_WORDS} words.",
        )
    if len(clean) > KEYWORD_VOLUME_MAX_LENGTH:
        raise KeywordVolumeError(
            "rrugc_keyword_too_long",
            f"Keyword must be {KEYWORD_VOLUME_MAX_LENGTH} characters or fewer.",
        )
    return clean, clean.casefold()


def provider_search_keyword(value: str) -> str:
    clean, _normalized = normalize_keyword(value)
    if re.search(r"\bhat\s*$", clean, flags=re.IGNORECASE):
        return clean
    return clean + " hat"


def normalize_keywords(values: list[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    rejected_short = 0
    for value in values:
        try:
            clean, normalized = normalize_keyword(value)
        except KeywordVolumeError as exc:
            if exc.code in {"rrugc_keyword_required", "rrugc_keyword_too_short"}:
                rejected_short += 1
                continue
            raise
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append((clean, normalized))
    if not result:
        if rejected_short:
            raise KeywordVolumeError(
                "rrugc_keyword_too_short",
                f"Keyword must contain at least {KEYWORD_VOLUME_MIN_WORDS} words.",
            )
        raise KeywordVolumeError(
            "rrugc_keywords_required",
            "At least one keyword is required.",
        )
    if len(result) > KEYWORD_VOLUME_MAX_BATCH:
        raise KeywordVolumeError(
            "rrugc_keyword_batch_too_large",
            f"At most {KEYWORD_VOLUME_MAX_BATCH} unique keywords can be checked at once.",
        )
    return result


def _as_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def _as_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _with_observed_volume_history(
    previous: dict | None,
    provider_item: dict,
    *,
    fetched_at: datetime,
    search_volume: int,
) -> dict:
    """Keep a bounded real observation series while preserving provider data."""
    # The hat-based volume refresh must never overwrite TM-only evidence.
    raw = {key: value for key, value in provider_item.items() if key != "trademark"}
    existing = previous or {}
    for key in ("trademark", "_trademark_screened_keyword"):
        if key in existing:
            raw[key] = existing[key]
    history = existing.get("_observed_volume_history")
    points: list[dict[str, object]] = []
    if isinstance(history, list):
        for point in history[-23:]:
            if not isinstance(point, dict):
                continue
            period = str(point.get("period") or "").strip()
            volume = point.get("volume")
            if not period:
                continue
            try:
                parsed = max(0, int(float(volume)))
            except (TypeError, ValueError):
                continue
            points.append({"period": period, "volume": parsed})

    period = fetched_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
    next_point = {"period": period, "volume": max(0, int(search_volume))}
    if points and points[-1]["period"] == period:
        points[-1] = next_point
    else:
        points.append(next_point)
    raw["_observed_volume_history"] = points[-24:]
    return raw


class RrugcKeywordVolumeService:
    def __init__(
        self,
        session: Session,
        *,
        http_client: httpx.AsyncClient | None = None,
        endpoint: str = KEYWORD_VOLUME_URL,
        cache_ttl: timedelta = KEYWORD_VOLUME_CACHE_TTL,
        sleeper=asyncio.sleep,
    ):
        self.session = session
        self.http_client = http_client
        self.endpoint = endpoint
        self.cache_ttl = cache_ttl
        self.sleeper = sleeper

    async def resolve(
        self,
        *,
        tenant_id: str,
        keywords: list[str],
        force: bool = False,
        source_image_url: str | None = None,
        source_pin_url: str | None = None,
        now: datetime | None = None,
    ) -> KeywordVolumeResolveResult:
        requested = normalize_keywords(keywords)
        requested_by_key = {normalized: clean for clean, normalized in requested}
        current_time = now or datetime.now(timezone.utc)
        fresh_after = current_time - self.cache_ttl

        existing_rows = list(
            self.session.scalars(
                select(RrugcKeywordVolumeModel).where(
                    RrugcKeywordVolumeModel.tenant_id == tenant_id,
                    RrugcKeywordVolumeModel.keyword_normalized.in_(
                        list(requested_by_key)
                    ),
                )
            )
        )
        existing_by_key = {
            row.keyword_normalized: row
            for row in existing_rows
        }

        # Persist every discovered quote before contacting the external
        # volume provider. A provider outage must not make a scanned quote
        # disappear from Stage 0 or from the database.
        for clean, normalized in requested:
            row = existing_by_key.get(normalized)
            if row is None:
                row = RrugcKeywordVolumeModel(
                    tenant_id=tenant_id,
                    keyword=clean,
                    keyword_normalized=normalized,
                    provider=KEYWORD_VOLUME_PENDING_PROVIDER,
                    request_count=0,
                    fetched_at=current_time,
                    last_requested_at=current_time,
                )
                self.session.add(row)
                existing_by_key[normalized] = row
            row.keyword = clean
            if source_image_url:
                row.source_image_url = source_image_url
            if source_pin_url:
                row.source_pin_url = source_pin_url
            row.last_requested_at = current_time
            row.request_count = int(row.request_count or 0) + 1
        self.session.commit()

        provider_keywords: list[str] = []
        cached_rows: list[RrugcKeywordVolumeModel] = []
        for clean, normalized in requested:
            row = existing_by_key[normalized]
            restore_cached_provider_trademark(row)
            fetched_at = _aware(row.fetched_at)
            provider_raw_keyword = str(
                (row.provider_raw_json or {}).get("keyword") or ""
            ).strip()
            provider_query_matches = (
                provider_raw_keyword.casefold()
                == provider_search_keyword(clean).casefold()
            )
            if (
                not force
                and row.provider == KEYWORD_VOLUME_PROVIDER
                and provider_query_matches
                # Preserve legacy verified non-zero cached rows, but retry
                # suspicious zeros whose provider payload had no measurement.
                and (
                    int(row.search_volume or 0) > 0
                    or (row.provider_raw_json or {}).get("search_volume") is not None
                )
                and fetched_at is not None
                and fetched_at >= fresh_after
            ):
                cached_rows.append(row)
            else:
                provider_keywords.append(clean)

        provider_payload: dict = {}
        provider_rows_by_key: dict[str, dict] = {}
        if provider_keywords:
            provider_search_terms = [
                provider_search_keyword(clean) for clean in provider_keywords
            ]
            provider_search_to_requested: dict[str, str] = {}
            for clean, search_term in zip(provider_keywords, provider_search_terms):
                _clean, requested_normalized = normalize_keyword(clean)
                _search_clean, search_normalized = normalize_keyword(search_term)
                provider_search_to_requested[search_normalized] = requested_normalized

            provider_payload = await self._fetch_provider(provider_search_terms)
            raw_data = provider_payload.get("data")
            if not isinstance(raw_data, list):
                raw_data = []
            for item in raw_data:
                if not isinstance(item, dict):
                    continue
                try:
                    _clean, normalized = normalize_keyword(
                        str(item.get("keyword") or "")
                    )
                except KeywordVolumeError:
                    continue
                requested_normalized = provider_search_to_requested.get(normalized)
                if requested_normalized is None and normalized in requested_by_key:
                    requested_normalized = normalized
                if requested_normalized is not None:
                    provider_rows_by_key[requested_normalized] = item

            for clean in provider_keywords:
                clean, normalized = normalize_keyword(clean)
                item = provider_rows_by_key.get(normalized)
                row = existing_by_key.get(normalized)
                if row is None:
                    continue
                # An HTTP 200 can omit individual keywords. Missing/invalid
                # volume is not a verified zero and must remain retryable.
                raw_volume = item.get("search_volume") if item else None
                try:
                    parsed_volume = float(raw_volume)
                    valid_volume = (
                        raw_volume is not None
                        and 0 <= parsed_volume < float("inf")
                    )
                except (TypeError, ValueError, OverflowError):
                    valid_volume = False
                if not valid_volume:
                    _LOGGER.warning(
                        "rrugc_keyword_volume_partial_result",
                        extra={"keyword_missing_or_invalid": True},
                    )
                    continue
                row.keyword = clean
                row.search_volume = _as_int(raw_volume)
                competition = str(item.get("competition") or "").strip().upper()
                row.competition = competition or None
                row.cpc_low = _as_float(item.get("cpc_low"))
                row.cpc_high = _as_float(item.get("cpc_high"))
                row.provider = KEYWORD_VOLUME_PROVIDER
                row.provider_account = (
                    str(provider_payload.get("account") or "").strip() or None
                )
                row.provider_customer_id = (
                    str(provider_payload.get("customer_id") or "").strip() or None
                )
                row.provider_raw_json = _with_observed_volume_history(
                    row.provider_raw_json,
                    item,
                    fetched_at=current_time,
                    search_volume=row.search_volume,
                )
                row.fetched_at = current_time
                row.last_requested_at = current_time

        # Independently check the ORIGINAL quote, never the hat-volume term.
        # A volume cache hit must not skip an invalid legacy trademark result.
        tm_keywords = [
            clean for clean, normalized in requested
            if force or not trademark_evidence_is_current(existing_by_key[normalized])
        ]
        if tm_keywords:
            try:
                await self.refresh_trademark(
                    tenant_id=tenant_id, keywords=tm_keywords, now=current_time
                )
            except KeywordVolumeError:
                _LOGGER.warning(
                    "rrugc_keyword_trademark_refresh_failed",
                    extra={"keyword_count": len(tm_keywords)},
                )
                # Volume metrics remain usable; TM stays unverified.

        self.session.commit()
        rows = list(
            self.session.scalars(
                select(RrugcKeywordVolumeModel)
                .where(
                    RrugcKeywordVolumeModel.tenant_id == tenant_id,
                    RrugcKeywordVolumeModel.keyword_normalized.in_(
                        list(requested_by_key)
                    ),
                )
                .order_by(
                    RrugcKeywordVolumeModel.search_volume.desc(),
                    RrugcKeywordVolumeModel.keyword.asc(),
                )
            )
        )
        return KeywordVolumeResolveResult(
            rows=rows,
            requested=len(requested),
            provider_requested=len(provider_keywords),
            cached=len(cached_rows),
        )

    async def refresh_trademark(
        self, *, tenant_id: str, keywords: list[str], now: datetime | None = None,
    ) -> tuple[int, int]:
        """Request trademark ONLY for original keywords; never mutate volume."""
        requested = normalize_keywords(keywords)
        rows = self.session.scalars(select(RrugcKeywordVolumeModel).where(
            RrugcKeywordVolumeModel.tenant_id == tenant_id,
            RrugcKeywordVolumeModel.keyword_normalized.in_([key for _, key in requested]),
        )).all()
        by_key = {row.keyword_normalized: row for row in rows}
        tm_payload = await self._fetch_provider(
            [original for original, _ in requested], tm_only=True,
        )
        data = tm_payload.get("data")
        matched_rows: set[str] = set()
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            raw_keyword = str(item.get("keyword") or "").strip()
            row = by_key.get(raw_keyword.casefold())
            if row is None or row.id in matched_rows:
                continue
            if apply_provider_trademark(
                row, item, checked_at=now or datetime.now(timezone.utc),
            ):
                matched_rows.add(row.id)
        self.session.commit()
        return len(matched_rows), len(requested)

    async def _fetch_provider(self, keywords: list[str], *, tm_only: bool = False) -> dict:
        owned_client = self.http_client is None
        client = self.http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"User-Agent": "CreativeAssetManager/rrugc-keyword-volume"},
        )
        try:
            last_error: Exception | None = None
            for attempt in range(KEYWORD_VOLUME_RETRIES + 1):
                try:
                    # Two independent requests: original terms for trademark,
                    # +hat terms for metrics. POST supports 50-term batches
                    # without risking a Pinterest-style HTTP 414 URL.
                    response = await client.post(
                        self.endpoint, json={"keywords": keywords},
                    )
                    if response.status_code in {404, 405}:
                        response = await client.get(
                            self.endpoint,
                            params={"keywords": ",".join(keywords)},
                        )
                    if response.status_code == 429 or response.status_code >= 500:
                        raise httpx.HTTPStatusError(
                            f"Keyword volume provider returned HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or payload.get("success") is not True:
                        raise KeywordVolumeError(
                            "rrugc_keyword_volume_invalid_response",
                            "Keyword volume provider returned an invalid response.",
                            status_code=502,
                        )
                    return payload
                except KeywordVolumeError:
                    raise
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    if attempt >= KEYWORD_VOLUME_RETRIES:
                        break
                    await self.sleeper(0.5 * (2 ** attempt))
            _LOGGER.warning(
                "rrugc_keyword_volume_provider_failed",
                extra={
                    "keyword_count": len(keywords),
                    "error_type": type(last_error).__name__ if last_error else None,
                },
            )
            raise KeywordVolumeError(
                "rrugc_keyword_volume_provider_unavailable",
                "Keyword volume provider is temporarily unavailable.",
                status_code=502,
            ) from last_error
        finally:
            if owned_client:
                await client.aclose()
