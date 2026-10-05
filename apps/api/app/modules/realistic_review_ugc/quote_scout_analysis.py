from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.providers.contracts import (
    AiMetadataAnalysisInput,
    AiMetadataProvider,
    AiProviderError,
)


QUOTE_SCOUT_PROFILE_VERSION = "rrugc-quote-scout-v1"
QUOTE_SCOUT_MAX_IMAGE_BYTES = 12 * 1024 * 1024
QUOTE_SCOUT_ALLOWED_IMAGE_HOST = re.compile(r"(^|\.)pinimg\.com$", re.IGNORECASE)


class QuoteScoutError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class HatQuoteDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_hat: bool
    quotes: list[str] = Field(default_factory=list, max_length=4)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("quotes", mode="before")
    @classmethod
    def normalize_quotes(cls, value):
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw in value[:8]:
            text = re.sub(r"\s+", " ", str(raw or "")).strip(" \t\r\n\"'“”")
            if len(text) < 2 or len(text) > 180:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
            if len(result) >= 4:
                break
        return result


@dataclass(frozen=True)
class QuoteScoutAnalysisResult:
    quotes: list[str]
    confidence: float
    provider: str
    model: str | None


def validate_pinterest_image_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not QUOTE_SCOUT_ALLOWED_IMAGE_HOST.search(host)
    ):
        raise QuoteScoutError(
            "quote_scout_image_url_rejected",
            "Quote Scout only accepts HTTPS Pinterest CDN images.",
        )
    return url


def _prompt(*, alt_text: str | None) -> str:
    alt = re.sub(r"\s+", " ", str(alt_text or "")).strip()[:500]
    alt_block = alt or "(none)"
    return f"""
You are reading text on a product photo discovered from Pinterest search results.

Goal: extract the exact saying/quote visibly printed or embroidered ON THE HAT/CAP itself.

Rules:
- Decide whether the image visibly contains a hat/cap.
- Read only wording physically on the hat/cap.
- Ignore Pinterest UI, captions, product titles, watermarks, packaging, signs, shirts,
  background text, comments, and any text not physically on the hat.
- Transcribe verbatim in natural reading order.
- Do not correct spelling, complete hidden letters, paraphrase, or invent missing words.
- If lettering is too unclear to read confidently, return no quote for it.
- If multiple separate text lines form one saying, combine them into one phrase.
- Return at most 4 distinct phrases.
- confidence is confidence that the returned phrase(s) are visibly present on the hat.

Pinterest image alt text is weak supporting context only and must never override visible evidence:
{alt_block}

Return exactly one JSON object matching the supplied schema and no prose.
""".strip()


async def fetch_pinterest_image(
    image_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    url = validate_pinterest_image_url(image_url)
    owned = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=6.0),
        follow_redirects=False,
        headers={"User-Agent": "CreativeAssetManager/rrugc-quote-scout"},
    )
    try:
        try:
            async with http.stream("GET", url) as response:
                if response.status_code != 200:
                    raise QuoteScoutError(
                        "quote_scout_image_fetch_failed",
                        f"Pinterest image returned HTTP {response.status_code}.",
                        status_code=502,
                    )
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type not in {
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                }:
                    raise QuoteScoutError(
                        "quote_scout_image_type_rejected",
                        "Pinterest image type is not supported.",
                        status_code=422,
                    )
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > QUOTE_SCOUT_MAX_IMAGE_BYTES:
                            raise QuoteScoutError(
                                "quote_scout_image_too_large",
                                "Pinterest image exceeds the Quote Scout size limit.",
                                status_code=413,
                            )
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > QUOTE_SCOUT_MAX_IMAGE_BYTES:
                        raise QuoteScoutError(
                            "quote_scout_image_too_large",
                            "Pinterest image exceeds the Quote Scout size limit.",
                            status_code=413,
                        )
                    chunks.append(chunk)
                if not chunks:
                    raise QuoteScoutError(
                        "quote_scout_image_empty",
                        "Pinterest image returned no data.",
                        status_code=502,
                    )
                return b"".join(chunks), content_type
        except QuoteScoutError:
            raise
        except httpx.HTTPError as exc:
            raise QuoteScoutError(
                "quote_scout_image_fetch_failed",
                "Pinterest image could not be downloaded.",
                status_code=502,
            ) from exc
    finally:
        if owned:
            await http.aclose()


async def analyze_hat_quote(
    *,
    provider: AiMetadataProvider,
    tenant_id: str,
    image_url: str,
    pin_url: str | None,
    alt_text: str | None,
    http_client: httpx.AsyncClient | None = None,
) -> QuoteScoutAnalysisResult:
    image_bytes, image_mime_type = await fetch_pinterest_image(
        image_url,
        client=http_client,
    )
    reference_id = "quote-scout:" + hashlib.sha256(
        (str(pin_url or "") + "\n" + image_url).encode("utf-8")
    ).hexdigest()[:32]
    try:
        result = await provider.analyze_single(
            AiMetadataAnalysisInput(
                tenant_id=tenant_id,
                asset_id=reference_id,
                prompt=_prompt(alt_text=alt_text),
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
                metadata_profile="rrugc_quote_scout",
                metadata_profile_version=QUOTE_SCOUT_PROFILE_VERSION,
                json_schema=HatQuoteDocument.model_json_schema(),
                analysis_id=reference_id,
            )
        )
    except AiProviderError:
        raise
    document = HatQuoteDocument.model_validate(dict(result.metadata))
    quotes = document.quotes if document.is_hat else []
    return QuoteScoutAnalysisResult(
        quotes=quotes,
        confidence=float(document.confidence),
        provider=result.provider,
        model=result.model,
    )
