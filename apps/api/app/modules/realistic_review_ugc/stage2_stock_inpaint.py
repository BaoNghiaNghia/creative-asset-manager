"""Reference-first Stage 2 inpaint: native provenance and protected stock pixels.

New Products jobs only. The reference is a real saved Slot 1 ImageGen Pass A
macro, not a synthetic raster/noisy fill or an untrusted external example.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.modules.image_generation.providers import GeneratedImageResult, PreparedImage


class Stage2InpaintError(ValueError):
    pass


def native_thread_macro(content: bytes) -> PreparedImage:
    """Pass A's PNG must remain byte-exact; normal image prep reencodes RGB to JPEG."""
    if not content or len(content) > 25_000_000:
        raise Stage2InpaintError("Missing or oversized native embroidery reference")
    try:
        with Image.open(BytesIO(content)) as source:
            width, height = source.size
            if (source.format != "PNG" or min(width, height) < 512
                    or width * height > 20_000_000):
                raise Stage2InpaintError("Expected a full-resolution native embroidery PNG")
            source.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise Stage2InpaintError("Corrupt native embroidery reference") from exc
    return PreparedImage(image_bytes=content, mime_type="image/png", width=width, height=height)


def _decoded(data: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP") or min(im.size) < 512:
                raise Stage2InpaintError("Invalid photo format or dimensions")
            im.load()
            return im.convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise Stage2InpaintError("Unrecognized or corrupt photo") from exc


def verified_inpaint_result(original: bytes, candidate: GeneratedImageResult,
                            kind: str) -> None:
    """Reject fake, wrong-sized or no-op native edits before any publishing."""
    actual_sha = sha256(candidate.image_bytes).hexdigest()
    if candidate.provider_metadata.get("native_sha256") != actual_sha:
        raise Stage2InpaintError("Missing byte-exact native ImageGen provenance")
    if sha256(original).hexdigest() == actual_sha:
        raise Stage2InpaintError("ImageGen did not apply any new embroidery")
    before, after = _decoded(original), _decoded(candidate.image_bytes)
    if (kind == "scene" and before.size != after.size) or (
        kind != "scene" and abs(before.width / before.height - after.width / after.height) > 0.015
    ):
        raise Stage2InpaintError("Native edit changed the original photo aspect ratio")
    if kind == "scene":
        a, b = np.asarray(before, dtype=np.int16), np.asarray(after, dtype=np.int16)
        delta = np.abs(a - b).mean(axis=2)
        changed = float(np.mean(delta > 25.0))
        if changed < 0.0002:
            raise Stage2InpaintError("No visible thread refinement in completed scene")
        if changed > 0.22:
            raise Stage2InpaintError("Inpaint repainted too much of the original scene")


def composite_original_stock(original: bytes, candidate: GeneratedImageResult,
                             stage: str) -> bytes:
    """Protect all factory photo pixels outside the narrow visible crown ROI.

    The model's visual inpaint mask is advisory; this deterministic photo
    composite is the actual protection boundary. Never paste an entire AI cap.
    """
    if stage not in {"prod_s2_ec2", "prod_s2_ec3"}:
        raise Stage2InpaintError("Unknown stock angle for inpaint")
    verified_inpaint_result(original, candidate, "stock")
    stock = _decoded(original)
    edited = _decoded(candidate.image_bytes)
    if stock.size != edited.size:
        edited = edited.resize(stock.size, Image.Resampling.LANCZOS)
    width, height = stock.size
    if stage == "prod_s2_ec2":
        # Three-quarter stock: perspective front crown, never brim/background.
        region = (0.22, 0.265, 0.81, 0.605)
    else:
        # True right-side: only the slim exposed edge of the front panel.
        # Never overwrite the official lateral GatorHats branding.
        region = (0.68, 0.29, 0.87, 0.565)
    mask = Image.new("L", stock.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        tuple(int(v * d) for v, d in zip(region, (width, height, width, height))),
        radius=max(3, int(min(width, height) * 0.015)), fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, min(width, height) / 600)))
    before = np.asarray(stock, dtype=np.int16)
    after = np.asarray(edited, dtype=np.int16)
    allowed = np.asarray(mask, dtype=np.uint8) > 64
    delta = np.abs(before - after).mean(axis=2)
    if np.count_nonzero((delta > 12) & allowed) < 120:
        raise Stage2InpaintError("No visible fine-thread edit in protected crown ROI")
    finished = Image.composite(edited, stock, mask)
    output = BytesIO()
    finished.save(output, format="PNG")
    return output.getvalue()
