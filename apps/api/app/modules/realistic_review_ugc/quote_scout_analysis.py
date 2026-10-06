from __future__ import annotations

import hashlib
import logging
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


QUOTE_SCOUT_PROFILE_VERSION = "rrugc-quote-scout-v5"
QUOTE_SCOUT_MAX_IMAGE_BYTES = 12 * 1024 * 1024
QUOTE_SCOUT_MIN_WORDS = 2
QUOTE_SCOUT_MAX_QUOTES_PER_IMAGE = 50
QUOTE_SCOUT_ALLOWED_IMAGE_HOST = re.compile(r"(^|\.)pinimg\.com$", re.IGNORECASE)
_QUOTE_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
_LOGGER = logging.getLogger("cam.rrugc.quote_scout")


def _quote_word_count(value: str) -> int:
    return len(_QUOTE_WORD_RE.findall(value))


class QuoteScoutError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class HatCapText(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lines: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "All readable text lines physically printed or embroidered on ONE "
            "target cap, in natural visual reading order. Different lines from "
            "the same cap must stay together in this single object."
        ),
    )

    @field_validator("lines", mode="before")
    @classmethod
    def normalize_lines(cls, value):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for raw in value:
            text = re.sub(r"\s+", " ", str(raw or "")).strip(" \t\r\n\"'“”")
            if not text or len(text) > 120:
                continue
            result.append(text)
            if len(result) >= 20:
                break
        return result


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
    cap_texts: list[HatCapText] = Field(
        default_factory=list,
        max_length=QUOTE_SCOUT_MAX_QUOTES_PER_IMAGE,
        description=(
            "One item per visible target cap. Never create one item per text "
            "line: every readable line belonging to the same cap must be grouped "
            "inside that cap's lines array."
        ),
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
        raw_groups = data.get("cap_texts")
        if not isinstance(raw_groups, list):
            for alias in ("caps", "hat_texts", "cap_groups", "hat_groups"):
                candidate = data.get(alias)
                if isinstance(candidate, list):
                    raw_groups = candidate
                    break

        cap_texts: list[dict[str, list[str]]] = []
        if isinstance(raw_groups, list):
            for group in raw_groups:
                if isinstance(group, dict):
                    confidence = group.get("confidence")
                    if isinstance(confidence, (int, float)):
                        phrase_confidences.append(float(confidence))
                    lines = group.get("lines")
                    if not isinstance(lines, list):
                        for alias in ("text_lines", "wording_lines"):
                            candidate = group.get(alias)
                            if isinstance(candidate, list):
                                lines = candidate
                                break
                    if not isinstance(lines, list):
                        text = (
                            group.get("quote")
                            or group.get("text")
                            or group.get("phrase")
                            or group.get("saying")
                        )
                        lines = [text] if text is not None else []
                else:
                    lines = [group]
                cap_texts.append({"lines": [str(line) for line in lines if line is not None]})
        else:
            # Backward compatibility for older provider/native payloads that
            # returned one flat quote per list item.
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
                        cap_texts.append({"lines": [str(text)]})

        data["cap_texts"] = cap_texts
        for alias in (
            "caps",
            "hat_texts",
            "cap_groups",
            "hat_groups",
            "quotes",
            "sayings",
            "phrases",
            "texts",
            "quote",
            "saying",
            "text",
        ):
            data.pop(alias, None)

        raw_confidence = data.get("confidence")
        if isinstance(raw_confidence, (int, float)):
            data["confidence"] = max(0.0, min(1.0, float(raw_confidence)))
        elif phrase_confidences:
            data["confidence"] = max(0.0, min(1.0, max(phrase_confidences)))
        else:
            data["confidence"] = 0.0
        return data

    @property
    def quotes(self) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for cap_text in self.cap_texts:
            text = re.sub(r"\s+", " ", " ".join(cap_text.lines)).strip(
                " \t\r\n\"'“”"
            )
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
  and return cap_texts=[].
- Do not stop after the first, clearest, largest, or central target cap.
- For EACH visible target cap, create exactly ONE item in cap_texts.
- Inside that item, put EVERY readable text line physically printed or embroidered on that SAME cap
  into its lines array, in natural visual reading order from top to bottom / left to right.
- NEVER create one cap_texts item per text line. The grouping unit is the physical cap, not the line.
- A cap with three readable lines must therefore produce one object with three lines. Example:
  ["GIRLS CAN GOLF TOO", "golf club", "SPORTY & RICH"].
  The server will concatenate those lines into ONE keyword:
  "GIRLS CAN GOLF TOO golf club SPORTY & RICH".
- If several target caps have different sayings, create one cap_texts item for each cap and keep
  the lines of each cap separate from the lines of every other cap.
- If target caps and other products appear together, read ONLY text physically on target caps.
- Ignore text on non-target hats, shirts, hoodies, bags, packaging, signs, Pinterest UI,
  captions, product titles, watermarks, comments, and background objects.
- Transcribe verbatim. Do not correct spelling, complete hidden letters, paraphrase, or invent missing words.
- If one line on a target cap is too unclear to read confidently, omit only that unreadable line and
  keep the other readable lines from that same cap.
- Preserve a readable one-word line when it belongs to a multi-line design on the cap; only the final
  concatenated keyword must contain at least 2 words.
- The transport supports up to 50 visible target caps per image; do not intentionally omit readable
  target caps unless that technical limit is reached.
- confidence is confidence that the grouped cap text is visibly present on TARGET CAPS.

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
    credential_providers: tuple[str | None, ...] | None = None,
) -> QuoteScoutAnalysisResult:
    image_bytes, image_mime_type = await fetch_pinterest_image(
        image_url,
        client=http_client,
    )
    reference_id = "quote-scout:" + hashlib.sha256(
        (str(pin_url or "") + "\n" + image_url).encode("utf-8")
    ).hexdigest()[:32]
    credentials = credential_providers or (None,)
    result = None
    last_provider_error: AiProviderError | None = None
    for attempt, credential_provider in enumerate(credentials, start=1):
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
                    preferred_credential_provider=credential_provider,
                )
            )
            if attempt > 1:
                _LOGGER.warning(
                    "quote_scout_provider_failover_recovered "
                    "credential_provider=%s attempt=%s total_candidates=%s",
                    credential_provider or "default",
                    attempt,
                    len(credentials),
                )
            break
        except AiProviderError as exc:
            last_provider_error = exc
            if not exc.retryable or attempt >= len(credentials):
                raise
            _LOGGER.warning(
                "quote_scout_provider_failover "
                "credential_provider=%s error_code=%s status_code=%s "
                "next_credential_provider=%s",
                credential_provider or "default",
                exc.code,
                exc.status_code,
                credentials[attempt] or "default",
            )
    if result is None:
        if last_provider_error is not None:
            raise last_provider_error
        raise AiProviderError(
            "Quote Scout vision provider is unavailable.",
            code="quote_scout_provider_unavailable",
            retryable=True,
            status_code=503,
        )
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
