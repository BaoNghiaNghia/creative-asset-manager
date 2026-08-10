from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


class InventoryImagePreparationError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class InventoryImagePreparationLimits:
    max_source_bytes: int
    max_source_width: int
    max_source_height: int
    max_decode_pixels: int
    max_output_bytes: int
    max_width: int
    max_height: int
    jpeg_quality: int


@dataclass(frozen=True, slots=True)
class PreparedInventoryImage:
    content: bytes
    content_sha256: str
    width: int
    height: int
    mime_type: str = "image/jpeg"


_ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "AVIF"})


def sha256_file(path: Path, *, max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise InventoryImagePreparationError("inventory_prepare_source_too_large")
            digest.update(chunk)
    return digest.hexdigest(), total


class StatelessInventoryImagePreparer:
    """Decode and normalize an Inventory source image without touching Creative state."""

    def __init__(self, limits: InventoryImagePreparationLimits):
        self.limits = limits

    def prepare(self, path: Path, *, expected_sha256: str, expected_size: int | None) -> PreparedInventoryImage:
        actual_sha256, actual_size = sha256_file(path, max_bytes=self.limits.max_source_bytes)
        if actual_sha256 != expected_sha256:
            raise InventoryImagePreparationError("inventory_prepare_source_hash_mismatch")
        if expected_size is not None and actual_size != expected_size:
            raise InventoryImagePreparationError("inventory_prepare_source_size_mismatch")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as opened:
                    source_format = (opened.format or "").upper()
                    if source_format not in _ALLOWED_FORMATS:
                        raise InventoryImagePreparationError("inventory_prepare_unsupported_image")
                    width, height = opened.size
                    if width <= 0 or height <= 0:
                        raise InventoryImagePreparationError("inventory_prepare_invalid_image")
                    if width > self.limits.max_source_width or height > self.limits.max_source_height:
                        raise InventoryImagePreparationError("inventory_prepare_source_dimensions")
                    if width * height > self.limits.max_decode_pixels:
                        raise InventoryImagePreparationError("inventory_prepare_decode_pixels")
                    if getattr(opened, "n_frames", 1) > 1:
                        opened.seek(0)
                    image = ImageOps.exif_transpose(opened)
                    image.load()
                    if image.mode not in {"RGB", "RGBA"}:
                        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                    if "A" in image.getbands():
                        background = Image.new("RGB", image.size, "white")
                        background.paste(image.convert("RGBA"), mask=image.getchannel("A"))
                        image = background
                    else:
                        image = image.convert("RGB")
                    image.thumbnail((self.limits.max_width, self.limits.max_height), Image.Resampling.LANCZOS)
                    if image.width <= 0 or image.height <= 0:
                        raise InventoryImagePreparationError("inventory_prepare_invalid_image")
                    output = io.BytesIO()
                    image.save(output, format="JPEG", quality=self.limits.jpeg_quality, optimize=True, progressive=True, exif=b"")
        except InventoryImagePreparationError:
            raise
        except Image.DecompressionBombError as exc:
            raise InventoryImagePreparationError("inventory_prepare_decode_pixels") from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise InventoryImagePreparationError("inventory_prepare_invalid_image") from exc
        content = output.getvalue()
        if len(content) > self.limits.max_output_bytes:
            raise InventoryImagePreparationError("inventory_prepare_output_too_large")
        return PreparedInventoryImage(
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
            width=image.width,
            height=image.height,
        )
