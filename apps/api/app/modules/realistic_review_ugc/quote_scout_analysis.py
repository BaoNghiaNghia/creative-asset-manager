from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.domain.providers.contracts import (
    AiMetadataAnalysisInput,
    AiMetadataProvider,
    AiProviderError,
)


QUOTE_SCOUT_PROFILE_VERSION = "rrugc-quote-scout-v4"
QUOTE_SCOUT_MAX_IMAGE_BYTES = 12 * 1024 * 1024
QUOTE_SCOUT_MIN_WORDS = 2
QUOTE_SCOUT_MAX_QUOTES_PER_IMAGE = 50
QUOTE_SCOUT_ALLOWED_IMAGE_HOST = re.compile(r"(^|\.)pinimg\.com$", re.IGNORECASE)
_QUOTE_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _quote_word_count(value: str) -> int:
    return len(_QUOTE_WORD_RE.findall(value))


class QuoteScoutError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class HatQuoteDocument(BaseModel):
    # Gemini can occasionally add harmless descriptive fields or use native
    # aliases. Ignore unknown metadata here; consumed fields remain validated.
    model_config = ConfigDict(extra="ignore")

    is_hat: bool = Field(
        default=False,
        description=(
            "True only when at least one target baseball-style cap is visible. "
            "Target caps include trucker, baseball, snapback, dad, 5-panel, "
            "6-panel, golf or sports caps with a crown and bill."
        ),
    )
    quotes: list[str] = Field(
        default_factory=list,
        max_length=QUOTE_SCOUT_MAX_QUOTES_PER_IMAGE,
    )
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_shape(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)

        # Gemini metadata adapters may return a native shape instead of the
        # supplied schema. All recognized boolean aliases map to the same
        # target-cap gate defined by the prompt.
        if "is_hat" not in data:
            for alias in (
                "is_target_cap",
                "has_target_cap",
                "has_cap",
                "is_cap",
                "has_hat",
            ):
                if alias in data:
                    data["is_hat"] = bool(data.get(alias))
                    break
        data.setdefault("is_hat", False)
        for alias in (
            "is_target_cap",
            "has_target_cap",
            "has_cap",
            "is_cap",
            "has_hat",
        ):
            data.pop(alias, None)

        phrase_confidences: list[float] = []
        raw_quotes = data.get("quotes")
        if not isinstance(raw_quotes, list):
            for alias in ("sayings", "phrases", "texts"):
                candidate = data.get(alias)
                if isinstance(candidate, list):
                    raw_quotes = candidate
                    break
        if not isinstance(raw_quotes, list):
            for alias in ("quote", "saying", "text"):
                candidate = data.get(alias)
                if isinstance(candidate, str) and candidate.strip():
                    raw_quotes = [candidate]
                    break

        if isinstance(raw_quotes, list):
            quotes: list[str] = []
            for phrase in raw_quotes:
                if isinstance(phrase, dict):
                    text = (
                        phrase.get("text")
                        or phrase.get("phrase")
                        or phrase.get("quote")
                        or phrase.get("saying")
                    )
                    confidence = phrase.get("confidence")
                    if isinstance(confidence, (int, float)):
                        phrase_confidences.append(float(confidence))
                else:
                    text = phrase
                if text is not None:
                    quotes.append(str(text))
            data["quotes"] = quotes
        else:
            data["quotes"] = []

        for alias in ("sayings", "phrases", "texts", "quote", "saying", "text"):
            data.pop(alias, None)

        raw_confidence = data.get("confidence")
        if isinstance(raw_confidence, (int, float)):
            data["confidence"] = max(0.0, min(1.0, float(raw_confidence)))
        elif phrase_confidences:
            data["confidence"] = max(0.0, min(1.0, max(phrase_confidences)))
        else:
            data["confidence"] = 0.0
        return data

    @field_validator("quotes", mode="before")
    @classmethod
    def normalize_quotes(cls, value):
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw in value:
            if isinstance(raw, dict):
                raw = raw.get("text") or raw.get("phrase") or ""
            text = re.sub(r"\s+", " ", str(raw or "")).strip(" \t\r\n\"'“”")
            if (
                _quote_word_count(text) < QUOTE_SCOUT_MIN_WORDS
                or len(text) > 180
            ):
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
            if len(result) >= QUOTE_SCOUT_MAX_QUOTES_PER_IMAGE:
                break
        return result


@dataclass(frozen=True)
class QuoteScoutAnalysisResult:
    quotes: list[str]
    is_target_cap: bool
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

Goal: extract exact sayings/quotes only from TARGET CAPS.

A TARGET CAP is a baseball-style cap with a crown and a front bill/visor. Allowed:
- trucker cap / mesh-back trucker hat
- baseball cap
- snapback
- dad cap
- 5-panel or 6-panel cap
- golf/sports cap when it has the same baseball-cap construction

NOT TARGET PRODUCTS - always reject these even when they contain text:
- beanie, knit hat, toque
- bucket hat
- cowboy/western hat
- fedora, trilby, pork-pie hat
- straw hat, sun hat, floppy/wide-brim hat
- visor-only headwear with no crown
- bonnet, beret
- helmet, hard hat
- shirts, hoodies, bags/totes, mugs, patches, stickers, posters, shoes, phone cases,
  or any other non-cap product

Rules:
- Inspect the ENTIRE image and identify every visible TARGET CAP, including small,
  side, background, partially angled, and non-central caps.
- is_hat means contains at least one TARGET CAP; it does NOT mean generic headwear.
- If the image contains only non-target hats/headwear or other products, set is_hat=false
  and return quotes=[].
- Do not stop after the first, clearest, largest, or central target cap.
- For EACH visible target cap, read the complete saying/quote physically printed or embroidered on that cap.
- If several target caps have different sayings, return every distinct readable saying from all of them.
- If target caps and other products appear together, read ONLY text physically on target caps.
- Ignore text on non-target hats, shirts, hoodies, bags, packaging, signs, Pinterest UI,
  captions, product titles, watermarks, comments, and background objects.
- Transcribe verbatim in natural reading order.
- Do not correct spelling, complete hidden letters, paraphrase, or invent missing words.
- If lettering on one target cap is too unclear to read confidently, skip only that unreadable quote;
  continue inspecting the other target caps.
- If multiple separate text lines on the same target cap form one saying, combine them into one phrase.
- Return every distinct readable target-cap saying. The transport supports up to 50 distinct quotes
  per image; do not intentionally omit readable target caps unless that technical limit is reached.
- Ignore single-word text, single letters, and fragments; each returned phrase must contain at least 2 words.
- Each item in quotes must be a plain string only. Never return {{text, confidence}} objects inside quotes.
- confidence is confidence that the returned phrase(s) are visibly present on TARGET CAPS.

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
    try:
        document = HatQuoteDocument.model_validate(dict(result.metadata))
    except ValidationError as exc:
        raise QuoteScoutError(
            "quote_scout_provider_payload_invalid",
            "Quote Scout vision provider returned an invalid metadata payload.",
            status_code=502,
        ) from exc
    quotes = document.quotes if document.is_hat else []
    return QuoteScoutAnalysisResult(
        quotes=quotes,
        is_target_cap=bool(document.is_hat),
        confidence=float(document.confidence),
        provider=result.provider,
        model=result.model,
    )
