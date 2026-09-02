from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ProviderKey = Literal["adobe_firefly", "cloudflare_sd", "gemini"]
PreservationMode = Literal["strict_expand", "semantic_expand"]
GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
CLOUDFLARE_SD_MODEL = "@cf/runwayml/stable-diffusion-v1-5-inpainting"

GEMINI_SQUARE_EXPANSION_INSTRUCTION = """Convert the provided image into a square 1:1 image by extending the surrounding scene naturally.

Preserve the original subject, identity, composition, pose, products, logos, visible text, colors, lighting, perspective and photographic style as faithfully as possible.
Do not crop the original subject.
Do not stretch the image.
Do not remove existing visible content.
Do not replace or redesign the primary subject.
Generate the additional visual content required around the existing image so that the final composition fills a square canvas naturally."""


@dataclass(frozen=True, slots=True)
class PreparedImage:
    image_bytes: bytes
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class GeneratedImageResult:
    provider: ProviderKey
    model: str | None
    image_bytes: bytes
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    provider_request_id: str | None = None
    provider_metadata: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class DeferredGenerationResult:
    provider: ProviderKey
    provider_job_id: str
    status_url: str
    cancel_url: str | None = None
    upload_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderPollResult:
    state: Literal["running", "succeeded", "failed", "cancelled"]
    output_url: str | None = None
    cancel_url: str | None = None
    retry_after_seconds: int = 10
    error_code: str | None = None


class SquareImageGenerationProvider(Protocol):
    provider_key: ProviderKey
    preservation_mode: PreservationMode

    async def generate_square(
        self, *, source: PreparedImage, target_size: int, prompt: str | None
    ) -> GeneratedImageResult | DeferredGenerationResult: ...


def gemini_expansion_prompt(user_prompt: str | None) -> str:
    value = (user_prompt or "").strip()
    if not value:
        return GEMINI_SQUARE_EXPANSION_INSTRUCTION
    return GEMINI_SQUARE_EXPANSION_INSTRUCTION + "\n\nUser preference:\n" + value


def gemini_image_size(target_size: int) -> Literal["1K", "2K"]:
    if target_size == 1024:
        return "1K"
    if target_size == 2048:
        return "2K"
    raise ValueError("image_generation_target_size_unsupported")
