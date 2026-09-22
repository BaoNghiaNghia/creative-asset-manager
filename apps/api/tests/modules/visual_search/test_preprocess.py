from __future__ import annotations

import io

import pytest
from PIL import Image
from pydantic import ValidationError

from app.modules.visual_search.preprocess import (
    VisualImagePreparationError,
    VisualPreprocessLimits,
    decode_visual_image,
    resize_for_encoder,
)
from app.modules.visual_search.schema import NormalizedCrop


def jpeg_bytes(
    *, size: tuple[int, int] = (80, 40), orientation: int | None = None
) -> bytes:
    image = Image.new("RGB", size, "red")
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


def mpo_bytes(*, size: tuple[int, int] = (80, 40)) -> bytes:
    first = Image.new("RGB", size, "red")
    second = Image.new("RGB", size, "blue")
    output = io.BytesIO()
    first.save(output, format="MPO", save_all=True, append_images=[second])
    return output.getvalue()


def heic_bytes(*, size: tuple[int, int] = (80, 40)) -> bytes:
    pillow_heif = pytest.importorskip("pillow_heif")

    output = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", size, "green")).save(output)
    return output.getvalue()


def test_decode_normalizes_exif_orientation_before_crop() -> None:
    decoded = decode_visual_image(
        jpeg_bytes(size=(80, 40), orientation=6),
        crop=NormalizedCrop(x=0, y=0, width=0.75, height=0.75),
    )
    assert (decoded.width, decoded.height) == (30, 60)
    assert decoded.image.mode == "RGB"


def test_decode_accepts_mpo_as_safe_multi_picture_jpeg() -> None:
    decoded = decode_visual_image(mpo_bytes())

    assert decoded.source_format == "MPO"
    assert (decoded.width, decoded.height) == (80, 40)
    assert decoded.image.mode == "RGB"
    red, _green, blue = decoded.image.getpixel((0, 0))
    assert red > blue


def test_decode_registers_heif_decoder_before_image_probe() -> None:
    decoded = decode_visual_image(heic_bytes())

    assert decoded.source_format in {"HEIF", "HEIC"}
    assert (decoded.width, decoded.height) == (80, 40)
    assert decoded.image.mode == "RGB"


def test_decode_rejects_byte_and_crop_limits() -> None:
    with pytest.raises(VisualImagePreparationError, match="byte limit"):
        decode_visual_image(
            jpeg_bytes(), limits=VisualPreprocessLimits(max_source_bytes=4)
        )
    with pytest.raises(VisualImagePreparationError, match="minimum pixel"):
        decode_visual_image(
            jpeg_bytes(size=(40, 40)),
            crop=NormalizedCrop(x=0, y=0, width=0.5, height=0.5),
            limits=VisualPreprocessLimits(
                min_crop_edge_pixels=30, min_crop_pixels=1
            ),
        )


def test_decode_rejects_invalid_input_and_resize_is_deterministic() -> None:
    with pytest.raises(VisualImagePreparationError, match="valid supported"):
        decode_visual_image(b"not an image")
    decoded = decode_visual_image(jpeg_bytes(size=(80, 40)))
    first = resize_for_encoder(decoded.image, width=16, height=16)
    second = resize_for_encoder(decoded.image, width=16, height=16)
    assert first.tobytes() == second.tobytes()
    assert first.size == (16, 16)


def test_crop_schema_rejects_bounds_before_decode() -> None:
    with pytest.raises(ValidationError):
        NormalizedCrop(x=0.9, y=0, width=0.2, height=0.5)
