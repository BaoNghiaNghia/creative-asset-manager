"""Cloudflare FLUX.2 Klein reference-conditioned image edits for Concept Short.

This is an opt-in, paid-capable provider: never silently substitute flat artwork,
synthetic stitches, text-to-image, or another API for a requested source edit.
"""
from __future__ import annotations

import base64
import binascii
from io import BytesIO

import httpx
from PIL import Image, ImageOps

from app.modules.image_generation.safe_download import MAX_IMAGE_BYTES

FLUX_KLEIN_MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
INPUT_MAX_SIDE = 504  # Cloudflare requires every input dimension to be < 512.


class CloudflareFluxEditError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class CloudflareFluxKleinImageEditProvider:
    """One source PNG and one original Concept Short prompt per API request."""

    def __init__(
        self, *, account_id: str, api_token: str,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.account_id = account_id.strip()
        self.api_token = api_token.strip()
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(180.0))
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def edit_reference(
        self, *, image_bytes: bytes, prompt: str,
        width: int = 1536, height: int = 1024,
    ) -> bytes:
        if not self.account_id or not self.api_token:
            raise CloudflareFluxEditError(
                "concept_short_flux_not_configured",
                "Cloudflare FLUX reference editing is not configured.",
            )
        if (width, height) != (1536, 1024) or not prompt.strip():
            raise CloudflareFluxEditError(
                "concept_short_flux_invalid_input",
                "Concept Short FLUX edit requires an original prompt and 1536x1024 canvas.",
            )
        try:
            with Image.open(BytesIO(image_bytes)) as source:
                source.load()
                source = ImageOps.exif_transpose(source).convert("RGB")
                source.thumbnail((INPUT_MAX_SIDE, INPUT_MAX_SIDE), Image.Resampling.LANCZOS)
                reference = BytesIO()
                source.save(reference, format="PNG", optimize=True)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise CloudflareFluxEditError(
                "concept_short_flux_source_invalid", "The original concept reference is invalid."
            ) from exc

        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
            f"/ai/run/{FLUX_KLEIN_MODEL}"
        )
        try:
            response = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_token}"},
                data={"prompt": prompt, "width": str(width), "height": str(height)},
                files={"input_image_0": ("source.png", reference.getvalue(), "image/png")},
            )
        except httpx.HTTPError as exc:
            raise CloudflareFluxEditError(
                "concept_short_flux_unavailable",
                "Cloudflare FLUX reference editing is temporarily unavailable.",
                retryable=True,
            ) from exc
        if response.status_code == 429:
            raise CloudflareFluxEditError(
                "concept_short_flux_rate_limited",
                "Cloudflare FLUX rate limit or daily inference quota reached.",
                retryable=True,
            )
        if response.status_code in (401, 403):
            raise CloudflareFluxEditError(
                "concept_short_flux_auth_failed",
                "Cloudflare FLUX credentials were rejected or this model is not authorized.",
            )
        if response.status_code >= 500:
            raise CloudflareFluxEditError(
                "concept_short_flux_unavailable",
                "Cloudflare FLUX service failed; the previous completed designs are preserved.",
                retryable=True,
            )
        if response.is_error:
            raise CloudflareFluxEditError(
                "concept_short_flux_rejected",
                f"Cloudflare FLUX rejected reference edit (HTTP {response.status_code}).",
            )
        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("success") is False:
                raise ValueError("Cloudflare reported an unsuccessful inference")
            result = payload.get("result") if isinstance(payload, dict) else None
            encoded = result.get("image") if isinstance(result, dict) else None
            if not isinstance(encoded, str) or not encoded:
                raise ValueError("Missing Cloudflare image result")
            image_data = base64.b64decode(encoded, validate=True)
            if not image_data or len(image_data) > MAX_IMAGE_BYTES:
                raise ValueError("Invalid image size")
            with Image.open(BytesIO(image_data)) as output:
                output.load()
                if output.size != (width, height):
                    raise ValueError("Cloudflare image dimensions were unexpected")
                # Only transcode the returned real model image, never paint
                # or simulate stitches in the application.
                normalized = BytesIO()
                output.convert("RGB").save(normalized, format="PNG")
        except (ValueError, KeyError, TypeError, OSError, binascii.Error,
                Image.DecompressionBombError) as exc:
            raise CloudflareFluxEditError(
                "concept_short_flux_invalid_response",
                "Cloudflare FLUX did not return a valid 1536x1024 edited image.",
            ) from exc
        data = normalized.getvalue()
        if len(data) > MAX_IMAGE_BYTES:
            raise CloudflareFluxEditError(
                "concept_short_flux_invalid_response", "Cloudflare FLUX image exceeds the output size limit."
            )
        return data
