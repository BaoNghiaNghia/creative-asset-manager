from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.domain.providers.contracts import (
    AssetStorageProvider,
    OpenStoredAssetInput,
    StorageProviderError,
)

logger = logging.getLogger(__name__)


class AnalysisImageError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AnalysisImageLimits:
    max_source_bytes: int = 25_000_000
    max_source_width: int = 20_000
    max_source_height: int = 20_000
    max_output_bytes: int = 8_000_000
    max_width: int = 2048
    max_height: int = 2048
    max_pixels: int = 4_194_304
    max_decode_pixels: int = 120_000_000
    jpeg_quality: int = 85


@dataclass(frozen=True, slots=True)
class PreparedAnalysisImage:
    content: bytes
    mime_type: str
    content_hash: str
    width: int
    height: int


class AnalysisImagePreparer:
    def __init__(
        self,
        storage_provider: AssetStorageProvider,
        *,
        limits: AnalysisImageLimits | None = None,
        temp_dir: str | None = None,
    ):
        self.storage_provider = storage_provider
        self.limits = limits or AnalysisImageLimits()
        self.temp_dir = temp_dir

    async def prepare(self, input: OpenStoredAssetInput) -> PreparedAnalysisImage:
        stream = None
        source_path: str | None = None
        output_path: str | None = None
        started = time.monotonic()
        reduced_decode = False
        source_format = "unknown"
        source_size = 0
        source_pixels = 0
        try:
            stream = await self.storage_provider.open_asset(input)
            with tempfile.NamedTemporaryFile(prefix="cam-analysis-source-", dir=self.temp_dir, delete=False) as source:
                source_path = source.name
                async for chunk in stream.body:
                    source_size += len(chunk)
                    if source_size > self.limits.max_source_bytes:
                        raise AnalysisImageError("Analysis source exceeds the byte limit.", code="analysis_image_too_large", retryable=False)
                    source.write(chunk)
            await stream.close()
            stream = None

            with Image.open(source_path) as opened:
                source_format = (opened.format or "").upper()
                if source_format not in {"JPEG", "MPO", "PNG", "WEBP", "TIFF", "AVIF", "HEIF", "HEIC", "GIF", "BMP"}:
                    raise AnalysisImageError("Managed asset is not a supported image format.", code="analysis_image_unsupported", retryable=False)
                source_width, source_height = opened.size
                source_pixels = source_width * source_height
                if source_width <= 0 or source_height <= 0 or source_width > self.limits.max_source_width or source_height > self.limits.max_source_height:
                    raise AnalysisImageError("Analysis source exceeds image dimension limits.", code="analysis_image_source_dimensions", retryable=False)
                if source_pixels > self.limits.max_decode_pixels:
                    raise AnalysisImageError("Analysis source exceeds the safe decode limit.", code="analysis_image_dimensions", retryable=False)

                image = opened
                if source_format in {"JPEG", "MPO"} and max(source_width, source_height) > max(self.limits.max_width, self.limits.max_height):
                    image.draft("RGB", (self.limits.max_width, self.limits.max_height))
                    reduced_decode = image.size != (source_width, source_height)
                image = ImageOps.exif_transpose(image)
                if getattr(image, "n_frames", 1) > 1:
                    image.seek(0)
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
                width, height = image.size
                if width <= 0 or height <= 0 or width > self.limits.max_width or height > self.limits.max_height or width * height > self.limits.max_pixels:
                    raise AnalysisImageError("Prepared image exceeds image dimension limits.", code="analysis_image_dimensions", retryable=False)
                with tempfile.NamedTemporaryFile(prefix="cam-analysis-ready-", suffix=".jpg", dir=self.temp_dir, delete=False) as output:
                    output_path = output.name
                image.save(output_path, format="JPEG", quality=self.limits.jpeg_quality, optimize=True, progressive=True, exif=b"")
            with open(output_path, "rb") as prepared:
                content = prepared.read(self.limits.max_output_bytes + 1)
            if len(content) > self.limits.max_output_bytes:
                raise AnalysisImageError("Prepared analysis image exceeds the byte limit.", code="analysis_image_output_too_large", retryable=False)
            logger.info("analysis_image_prepared format=%s source_width=%s source_height=%s source_bytes=%s source_pixels=%s decoder=%s reduced_decode=%s prepared_width=%s prepared_height=%s prepared_bytes=%s duration_ms=%s", source_format, source_width, source_height, source_size, source_pixels, "pillow", reduced_decode, width, height, len(content), round((time.monotonic() - started) * 1000))
            return PreparedAnalysisImage(content=content, mime_type="image/jpeg", content_hash=hashlib.sha256(content).hexdigest(), width=width, height=height)
        except AnalysisImageError:
            raise
        except StorageProviderError as exc:
            error_map = {"managed_storage_object_missing": "analysis_storage_object_missing", "managed_storage_unauthorized": "analysis_storage_access_denied", "managed_storage_forbidden": "analysis_storage_access_denied", "managed_storage_temporarily_unavailable": "analysis_storage_temporarily_unavailable", "managed_storage_network_error": "analysis_storage_temporarily_unavailable"}
            raise AnalysisImageError("Managed asset could not be read.", code=error_map.get(exc.code, "analysis_storage_read_failed"), retryable=exc.retryable) from exc
        except Image.DecompressionBombError as exc:
            raise AnalysisImageError("Analysis source exceeds the safe decode limit.", code="analysis_image_dimensions", retryable=False) from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise AnalysisImageError("Managed asset is not a valid supported image.", code="analysis_image_invalid", retryable=False) from exc
        finally:
            if stream is not None:
                await stream.close()
            for path in (source_path, output_path):
                if path is not None:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
