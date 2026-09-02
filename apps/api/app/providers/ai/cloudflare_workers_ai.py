from __future__ import annotations

import base64
from io import BytesIO

import httpx
from PIL import Image, ImageOps

from app.modules.image_generation.providers import (
    CLOUDFLARE_SD_MODEL,
    GeneratedImageResult,
    PreparedImage,
)
from app.modules.image_generation.safe_download import ALLOWED_IMAGE_MIME_TYPES, MAX_IMAGE_BYTES

CLOUDFLARE_AI_BASE_URL = "https://api.cloudflare.com/client/v4/accounts"


class CloudflareImageProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class CloudflareSquareImageProvider:
    """Cloudflare Workers AI img2img expansion. It never falls back to another provider."""

    provider_key = "cloudflare_sd"
    preservation_mode = "semantic_expand"

    def __init__(self, *, account_id: str, api_token: str, http_client: httpx.AsyncClient | None = None):
        self.account_id = account_id.strip()
        self.api_token = api_token.strip()
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        self._owns_client = http_client is None

    @property
    def available(self) -> bool:
        return bool(self.account_id and self.api_token)

    async def generate_square(self, *, source: PreparedImage, target_size: int, prompt: str | None) -> GeneratedImageResult:
        if not self.available:
            raise CloudflareImageProviderError("cloudflare_sd_not_configured", "Cloudflare SD is not configured.")
        if target_size not in (1024, 2048):
            raise CloudflareImageProviderError("image_generation_target_size_unsupported", "Unsupported square target size.")
        canvas = _square_canvas(source, target_size)
        payload = {
            "prompt": _prompt(prompt),
            "negative_prompt": "cropped subject, distorted subject, changed logo, changed text, watermark, frame, border",
            "width": target_size,
            "height": target_size,
            "image_b64": base64.b64encode(canvas).decode("ascii"),
            "strength": 0.45,
            "guidance": 7.5,
            "num_steps": 20,
        }
        url = f"{CLOUDFLARE_AI_BASE_URL}/{self.account_id}/ai/run/{CLOUDFLARE_SD_MODEL}"
        try:
            response = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise CloudflareImageProviderError("cloudflare_sd_unavailable", "Cloudflare SD is temporarily unavailable.", retryable=True) from exc
        if response.status_code == 429:
            raise CloudflareImageProviderError("cloudflare_sd_rate_limited", "Cloudflare SD daily free quota is exhausted.", retryable=True)
        if response.status_code in {401, 403}:
            raise CloudflareImageProviderError("cloudflare_sd_auth_failed", "Cloudflare SD credentials were rejected.")
        if response.status_code >= 500:
            raise CloudflareImageProviderError("cloudflare_sd_unavailable", "Cloudflare SD is temporarily unavailable.", retryable=True)
        if response.is_error:
            raise CloudflareImageProviderError("cloudflare_sd_generation_failed", "Cloudflare SD rejected the image generation request.")
        data, mime = _result_image(response)
        _validate_output(data, mime, target_size)
        request_id = response.headers.get("cf-ray")
        return GeneratedImageResult(provider="cloudflare_sd", model=CLOUDFLARE_SD_MODEL, image_bytes=data, mime_type=mime, provider_request_id=request_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _prompt(value: str | None) -> str:
    instruction = "Extend this image naturally into a square 1:1 composition. Preserve the source subject, products, logos, visible text, colors, lighting, perspective and photographic style. Fill only the surrounding canvas."
    return instruction + (" " + value.strip() if value and value.strip() else "")


def _square_canvas(source: PreparedImage, target_size: int) -> bytes:
    try:
        with Image.open(BytesIO(source.image_bytes)) as decoded:
            image = ImageOps.exif_transpose(decoded).convert("RGB")
            image.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (target_size, target_size), "#777777")
            left = (target_size - image.width) // 2
            top = (target_size - image.height) // 2
            canvas.paste(image, (left, top))
            output = BytesIO()
            canvas.save(output, "PNG")
            return output.getvalue()
    except Exception as exc:
        raise CloudflareImageProviderError("cloudflare_sd_source_invalid", "Source image cannot be prepared.") from exc


def _result_image(response: httpx.Response) -> tuple[bytes, str]:
    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if mime in ALLOWED_IMAGE_MIME_TYPES:
        return response.content, mime
    try:
        payload = response.json()
        result = payload.get("result", payload) if isinstance(payload, dict) else {}
        encoded = result.get("image") or result.get("image_b64")
        if not isinstance(encoded, str):
            raise ValueError
        return base64.b64decode(encoded, validate=True), "image/png"
    except (ValueError, TypeError):
        raise CloudflareImageProviderError("cloudflare_sd_invalid_response", "Cloudflare SD returned no valid generated image.")


def _validate_output(data: bytes, mime: str, target_size: int) -> None:
    if mime not in ALLOWED_IMAGE_MIME_TYPES or not data or len(data) > MAX_IMAGE_BYTES:
        raise CloudflareImageProviderError("cloudflare_sd_invalid_response", "Cloudflare SD returned an unsupported generated image.")
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            actual = Image.MIME.get(image.format or "", "").lower()
            width, height = image.size
    except Exception as exc:
        raise CloudflareImageProviderError("cloudflare_sd_invalid_response", "Cloudflare SD returned an invalid generated image.") from exc
    if actual != mime or width != target_size or height != target_size:
        raise CloudflareImageProviderError("cloudflare_sd_invalid_response", "Cloudflare SD returned an image with invalid format or dimensions.")
