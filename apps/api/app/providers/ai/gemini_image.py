from __future__ import annotations

import base64
from io import BytesIO

import httpx
from PIL import Image

from app.modules.image_generation.providers import (
    GEMINI_IMAGE_MODEL,
    GeneratedImageResult,
    PreparedImage,
    gemini_expansion_prompt,
    gemini_image_size,
)
from app.modules.image_generation.safe_download import ALLOWED_IMAGE_MIME_TYPES, MAX_IMAGE_BYTES


class GeminiImageProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _safe_google_error_message(response: httpx.Response, fallback: str) -> str:
    """Keep a bounded provider diagnostic without exposing request content or credentials."""
    try:
        payload = response.json()
    except ValueError:
        return fallback
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return fallback
    normalized = " ".join(message.split())
    return normalized[:500] or fallback


class GeminiSquareImageProvider:
    """Synchronous semantic expansion using only gemini-3.1-flash-image."""

    provider_key = "gemini"
    preservation_mode = "semantic_expand"

    def __init__(self, *, api_key: str, http_client: httpx.AsyncClient | None = None):
        self.api_key = api_key.strip()
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        self._owns_client = http_client is None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def generate_square(
        self, *, source: PreparedImage, target_size: int, prompt: str | None
    ) -> GeneratedImageResult:
        if not self.available:
            raise GeminiImageProviderError("gemini_image_not_configured", "Gemini image generation is not configured.")
        try:
            image_size = gemini_image_size(target_size)
        except ValueError as exc:
            raise GeminiImageProviderError(str(exc), "Unsupported square target size.") from exc
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_IMAGE_MODEL}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": gemini_expansion_prompt(prompt)},
                        {
                            "inlineData": {
                                "mimeType": source.mime_type,
                                "data": base64.b64encode(source.image_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": "1:1", "imageSize": image_size},
            },
        }
        try:
            response = await self._client.post(
                url,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise GeminiImageProviderError(
                "gemini_image_provider_unavailable",
                "Gemini image generation is temporarily unavailable.",
                retryable=True,
            ) from exc
        if response.status_code == 429:
            raise GeminiImageProviderError(
                "gemini_image_rate_limited",
                _safe_google_error_message(
                    response, "Gemini image generation rate limit reached."
                ),
                retryable=True,
            )
        if response.status_code >= 500:
            raise GeminiImageProviderError(
                "gemini_image_provider_unavailable",
                "Gemini image generation is temporarily unavailable.",
                retryable=True,
            )
        if response.status_code in {401, 403}:
            raise GeminiImageProviderError(
                "gemini_image_auth_failed",
                "Gemini image credentials were rejected.",
            )
        if response.is_error:
            raise GeminiImageProviderError(
                "gemini_image_generation_failed",
                "Gemini rejected the image generation request.",
            )
        try:
            body = response.json()
            parts = body["candidates"][0]["content"]["parts"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise GeminiImageProviderError(
                "gemini_image_invalid_response",
                "Gemini returned an invalid image response.",
            )
        image_part = next(
            (
                part.get("inlineData") or part.get("inline_data")
                for part in parts
                if isinstance(part, dict) and (part.get("inlineData") or part.get("inline_data"))
            ),
            None,
        )
        try:
            mime = str(image_part["mimeType"]).split(";", 1)[0].lower()
            data = base64.b64decode(image_part["data"], validate=True)
        except (TypeError, KeyError, ValueError):
            raise GeminiImageProviderError(
                "gemini_image_invalid_response",
                "Gemini returned no valid generated image.",
            )
        if mime not in ALLOWED_IMAGE_MIME_TYPES or not data or len(data) > MAX_IMAGE_BYTES:
            raise GeminiImageProviderError(
                "gemini_image_invalid_response",
                "Gemini returned an unsupported generated image.",
            )
        try:
            with Image.open(BytesIO(data)) as image:
                image.load()
                actual_mime = Image.MIME.get(image.format or "", "").lower()
                width, height = image.size
        except Exception as exc:
            raise GeminiImageProviderError(
                "gemini_image_invalid_response",
                "Gemini returned an invalid generated image.",
            ) from exc
        if actual_mime != mime or width != height or width != target_size:
            raise GeminiImageProviderError(
                "gemini_image_invalid_response",
                "Gemini returned an image with invalid format or dimensions.",
            )
        request_id = response.headers.get("x-request-id") or response.headers.get("x-goog-request-id")
        return GeneratedImageResult(
            provider="gemini",
            model=GEMINI_IMAGE_MODEL,
            image_bytes=data,
            mime_type=mime,
            provider_request_id=request_id,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
