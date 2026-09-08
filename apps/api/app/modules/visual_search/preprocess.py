from __future__ import annotations

import io
import math
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError, features

from app.modules.ai_metadata.image_codecs import register_heif_decoder
from app.modules.visual_search.schema import NormalizedCrop


class VisualImagePreparationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VisualPreprocessLimits:
    max_source_bytes: int = 25_000_000
    max_source_width: int = 20_000
    max_source_height: int = 20_000
    max_decode_pixels: int = 120_000_000
    min_crop_edge_pixels: int = 16
    min_crop_pixels: int = 1_024

    def __post_init__(self) -> None:
        if min(
            self.max_source_bytes,
            self.max_source_width,
            self.max_source_height,
            self.max_decode_pixels,
            self.min_crop_edge_pixels,
            self.min_crop_pixels,
        ) <= 0:
            raise ValueError("visual preprocessing limits must be positive")


@dataclass(frozen=True, slots=True)
class PreparedVisualImage:
    """Decoded RGB image after EXIF normalization and any requested crop."""

    image: Image.Image
    source_format: str
    width: int
    height: int


_ALLOWED_FORMATS = frozenset(
    {"JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF", "AVIF", "HEIF", "HEIC"}
)


def decode_visual_image(
    content: bytes,
    *,
    limits: VisualPreprocessLimits | None = None,
    crop: NormalizedCrop | None = None,
) -> PreparedVisualImage:
    """Safely decode image bytes; callers never provide a filesystem path or URL."""

    limits = limits or VisualPreprocessLimits()
    if not content:
        raise VisualImagePreparationError("visual_image_empty", "Visual-search image is empty.")
    if len(content) > limits.max_source_bytes:
        raise VisualImagePreparationError(
            "visual_image_too_large", "Visual-search image exceeds the byte limit."
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as probe:
                source_format = (probe.format or "").upper()
                _validate_format(source_format)
                _validate_dimensions(*probe.size, limits)
                probe.verify()

            with Image.open(io.BytesIO(content)) as opened:
                _validate_dimensions(*opened.size, limits)
                if getattr(opened, "n_frames", 1) > 1:
                    opened.seek(0)
                oriented = ImageOps.exif_transpose(opened)
                oriented.load()
                image = _to_rgb(oriented)
    except VisualImagePreparationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise VisualImagePreparationError(
            "visual_image_decode_pixels",
            "Visual-search image exceeds the decoded-pixel limit.",
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise VisualImagePreparationError(
            "visual_image_invalid", "Visual-search image is not a valid supported image."
        ) from exc

    if crop is not None:
        image = crop_visual_image(image, crop, limits=limits)
    return PreparedVisualImage(
        image=image,
        source_format=source_format,
        width=image.width,
        height=image.height,
    )


def crop_visual_image(
    image: Image.Image,
    crop: NormalizedCrop,
    *,
    limits: VisualPreprocessLimits,
) -> Image.Image:
    """Crop EXIF-normalized pixels from normalized request coordinates."""

    left = math.floor(crop.x * image.width)
    top = math.floor(crop.y * image.height)
    right = math.ceil((crop.x + crop.width) * image.width)
    bottom = math.ceil((crop.y + crop.height) * image.height)
    width, height = right - left, bottom - top
    if (
        width < limits.min_crop_edge_pixels
        or height < limits.min_crop_edge_pixels
        or width * height < limits.min_crop_pixels
    ):
        raise VisualImagePreparationError(
            "visual_crop_too_small", "Visual-search crop is below the minimum pixel size."
        )
    return image.crop((left, top, right, bottom))


def resize_for_encoder(
    image: Image.Image,
    *,
    width: int,
    height: int,
) -> Image.Image:
    """Deterministic center-crop resize; model adapters own normalization values."""

    if width <= 0 or height <= 0:
        raise ValueError("encoder target dimensions must be positive")
    return ImageOps.fit(
        image,
        (width, height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def _validate_format(source_format: str) -> None:
    if source_format not in _ALLOWED_FORMATS:
        raise VisualImagePreparationError(
            "visual_image_unsupported", "Visual-search image format is unsupported."
        )
    if source_format == "AVIF" and not features.check("avif"):
        raise VisualImagePreparationError(
            "visual_avif_decoder_unavailable", "No safe AVIF decoder is available."
        )
    if source_format in {"HEIF", "HEIC"} and not register_heif_decoder():
        raise VisualImagePreparationError(
            "visual_heif_decoder_unavailable", "No safe HEIF decoder is available."
        )


def _validate_dimensions(
    width: int, height: int, limits: VisualPreprocessLimits
) -> None:
    if width <= 0 or height <= 0:
        raise VisualImagePreparationError(
            "visual_image_invalid", "Visual-search image dimensions are invalid."
        )
    if width > limits.max_source_width or height > limits.max_source_height:
        raise VisualImagePreparationError(
            "visual_image_dimensions", "Visual-search image exceeds dimension limits."
        )
    if width * height > limits.max_decode_pixels:
        raise VisualImagePreparationError(
            "visual_image_decode_pixels",
            "Visual-search image exceeds the decoded-pixel limit.",
        )


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    if "A" not in image.getbands():
        return image.convert("RGB")
    background = Image.new("RGB", image.size, "white")
    rgba = image.convert("RGBA")
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background
