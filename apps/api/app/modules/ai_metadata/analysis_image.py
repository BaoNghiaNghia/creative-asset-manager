from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.domain.providers.contracts import (
    AssetStorageProvider,
    OpenStoredAssetInput,
    StorageProviderError,
)


class AnalysisImageError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AnalysisImageLimits:
    max_source_bytes: int = 25_000_000
    max_output_bytes: int = 8_000_000
    max_width: int = 4096
    max_height: int = 4096
    max_pixels: int = 24_000_000
    jpeg_quality: int = 88


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
        try:
            stream = await self.storage_provider.open_asset(input)
            with tempfile.NamedTemporaryFile(
                prefix="cam-analysis-source-",
                dir=self.temp_dir,
                delete=False,
            ) as source:
                source_path = source.name
                received = 0
                async for chunk in stream.body:
                    received += len(chunk)
                    if received > self.limits.max_source_bytes:
                        raise AnalysisImageError(
                            "Analysis source exceeds the byte limit.",
                            code="analysis_image_too_large",
                            retryable=False,
                        )
                    source.write(chunk)
            await stream.close()
            stream = None
            with Image.open(source_path) as opened:
                width, height = opened.size
                if (
                    width <= 0
                    or height <= 0
                    or width > self.limits.max_width * 8
                    or height > self.limits.max_height * 8
                    or width * height > self.limits.max_pixels
                ):
                    raise AnalysisImageError(
                        "Analysis source exceeds image dimension limits.",
                        code="analysis_image_dimensions",
                        retryable=False,
                    )
                image = ImageOps.exif_transpose(opened)
                image.load()
                if image.mode not in {"RGB", "L"}:
                    if "A" in image.getbands():
                        background = Image.new("RGB", image.size, "white")
                        alpha = image.getchannel("A")
                        background.paste(image.convert("RGB"), mask=alpha)
                        image = background
                    else:
                        image = image.convert("RGB")
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                image.thumbnail(
                    (self.limits.max_width, self.limits.max_height),
                    Image.Resampling.LANCZOS,
                )
                width, height = image.size
                with tempfile.NamedTemporaryFile(
                    prefix="cam-analysis-ready-",
                    suffix=".jpg",
                    dir=self.temp_dir,
                    delete=False,
                ) as output:
                    output_path = output.name
                image.save(
                    output_path,
                    format="JPEG",
                    quality=self.limits.jpeg_quality,
                    optimize=True,
                    exif=b"",
                )
            with open(output_path, "rb") as prepared:
                content = prepared.read(self.limits.max_output_bytes + 1)
            if len(content) > self.limits.max_output_bytes:
                raise AnalysisImageError(
                    "Prepared analysis image exceeds the byte limit.",
                    code="analysis_image_output_too_large",
                    retryable=False,
                )
            return PreparedAnalysisImage(
                content=content,
                mime_type="image/jpeg",
                content_hash=hashlib.sha256(content).hexdigest(),
                width=width,
                height=height,
            )
        except AnalysisImageError:
            raise
        except StorageProviderError as exc:
            raise AnalysisImageError(
                "Managed asset could not be read.",
                code="analysis_storage_read_failed",
                retryable=exc.retryable,
            ) from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise AnalysisImageError(
                "Managed asset is not a valid supported image.",
                code="analysis_image_invalid",
                retryable=False,
            ) from exc
        finally:
            if stream is not None:
                await stream.close()
            for path in (source_path, output_path):
                if path is not None:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
