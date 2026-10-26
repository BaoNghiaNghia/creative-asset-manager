"""Fabric-first 2048x1024 embroidery output with verifiable thread geometry.

The public PNG has an opaque woven-cotton background. A small, compressed and
hash-checked thread geometry payload remains INSIDE the same PNG for CAM's
front placement renderer. No independent alpha asset is published or needed.
Historical RGBA masters remain readable through the legacy path.
"""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import zlib
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from PIL.PngImagePlugin import PngInfo
from app.modules.realistic_review_ugc.stage1_photo_samples import reference_patch

FABRIC_KEY = "cam.fabric_first.thread_geometry.v1"
FABRIC_SHA_KEY = "cam.fabric_first.thread_sha256.v1"
FABRIC_RENDER_VERSION = "fabric_first_twill_v1"
MAX_CANONICAL_BYTES = 8_000_000
MAX_METADATA_LENGTH = 4_000_000


def _woven_tile() -> Image.Image:
    # The photographed 64px thread-free twill patch is supplied by the user.
    # Colorize only the neutral reference swatch; actual cap color is supplied
    # exclusively by the official 8869 photo in Stage 2.
    sampled = reference_patch("fabric")
    def tone(base: int) -> Image.Image:
        return sampled.point([max(0, min(255, round(base + (v - 200) * .60)))
                              for v in range(256)])
    return Image.merge("RGB", (tone(229), tone(223), tone(218)))


def render_fabric_reference(stitched: Image.Image) -> Image.Image:
    """Create the RGB display/output image. Only thread geometry is reusable."""
    if stitched.mode != "RGBA" or stitched.getchannel("A").getbbox() is None:
        raise ValueError("Fabric reference requires non-empty stitched RGBA geometry")
    w, h = stitched.size
    tile = _woven_tile()
    cloth = Image.new("RGB", (w, h))
    flip = tile.transpose(Image.Transpose.ROTATE_180)
    for y in range(0, h, 64):
        for x in range(0, w, 64):
            cloth.paste(flip if (x // 64 + y // 64) % 2 else tile, (x, y))
    # Tiny weave depression immediately at needle contact, not an extrusion.
    alpha = stitched.getchannel("A")
    contact = ImageChops.subtract(
        alpha.filter(ImageFilter.GaussianBlur(2.0)), alpha
    ).point(lambda v: round(v * .26))
    cloth = Image.composite(Image.new("RGB", (w, h), (126, 118, 108)),
                            cloth, contact)
    # One-pixel uneven needle ends at the satin boundary. They contribute to
    # the photograph only: embedded artwork geometry remains bit-for-bit exact.
    boundary = ImageChops.subtract(alpha.filter(ImageFilter.MaxFilter(3)), alpha)
    thread_noise = reference_patch("thread").point(
        [min(255, max(0, (v - 10) * 3)) for v in range(256)])
    speckle = Image.new("L", (w, h))
    for yy in range(0, h, 64):
        for xx in range(0, w, 64):
            speckle.paste(thread_noise, (xx, yy))
    fringe = ImageChops.multiply(boundary, speckle).point(
        [round(v * .30) for v in range(256)])
    cloth.paste(stitched.convert("RGB").filter(ImageFilter.MaxFilter(3)),
                (0, 0), fringe)
    cloth.paste(stitched.convert("RGB"), (0, 0), alpha)
    return cloth


def _canonical_payload(stitched: Image.Image) -> tuple[str, str]:
    out = BytesIO()
    stitched.save(out, format="PNG", optimize=True)
    raw = out.getvalue()
    if len(raw) > MAX_CANONICAL_BYTES:
        raise ValueError("Thread geometry exceeds maximum size")
    return (base64.b64encode(zlib.compress(raw, 9)).decode("ascii"),
            hashlib.sha256(raw).hexdigest())


def save_fabric_output(stitched: Image.Image, destination: Path) -> None:
    """Publish ONE opaque PNG with signed-by-hash embedded canonical thread data."""
    opaque = render_fabric_reference(stitched)
    payload, sha = _canonical_payload(stitched)
    if len(payload) > MAX_METADATA_LENGTH:
        raise ValueError("Embedded thread geometry is too large")
    metadata = PngInfo()
    metadata.add_text(FABRIC_KEY, payload)
    metadata.add_text(FABRIC_SHA_KEY, sha)
    metadata.add_text("cam.fabric_first.version", FABRIC_RENDER_VERSION)
    opaque.save(destination, format="PNG", optimize=True, pnginfo=metadata)


def extract_thread_geometry(opaque: Image.Image) -> Image.Image | None:
    """Decode a verified embedded geometry payload; None means legacy input.

    Fail closed on malformed/tampered tagged images. Never try to classify
    fabric fibers as embroidery if metadata is broken.
    """
    payload = opaque.info.get(FABRIC_KEY)
    if payload is None:
        return None
    if (not isinstance(payload, str) or len(payload) > MAX_METADATA_LENGTH
            or not isinstance(opaque.info.get(FABRIC_SHA_KEY), str)):
        raise ValueError("Fabric-first thread geometry metadata is invalid")
    try:
        compressed = base64.b64decode(payload, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_CANONICAL_BYTES + 1)
        if len(raw) > MAX_CANONICAL_BYTES or decoder.unconsumed_tail or not decoder.eof:
            raise ValueError("Thread geometry over limit or truncated")
        raw += decoder.flush()
        if (len(raw) > MAX_CANONICAL_BYTES
                or hashlib.sha256(raw).hexdigest() != opaque.info[FABRIC_SHA_KEY]):
            raise ValueError("Thread geometry checksum mismatch")
        with Image.open(BytesIO(raw)) as decoded:
            decoded.load()
            if (decoded.format != "PNG" or decoded.mode != "RGBA"
                    or decoded.size != opaque.size or decoded.getchannel("A").getbbox() is None):
                raise ValueError("Thread geometry shape mismatch")
            return decoded.copy()
    except (OSError, ValueError, TypeError, zlib.error) as exc:
        raise ValueError("Corrupted embedded embroidery geometry") from exc


def fabric_output_bytes(stitched: Image.Image) -> bytes:
    output = BytesIO()
    save_fabric_output(stitched, output)
    return output.getvalue()


def thread_preview_bytes(original_bytes: bytes, size_pixels: int | None = None) -> bytes:
    """Temporary browser-only thread geometry view, never a published design.

    The API sends this to existing embroidery-on-cap canvas components, while
    the gallery, downloads and canonical Stage 1 outputs remain opaque RGB.
    """
    if not original_bytes or len(original_bytes) > 20 * 1024 * 1024:
        raise ValueError("Stage 1 artwork exceeds preview limits")
    with Image.open(BytesIO(original_bytes)) as source:
        source.load()
        if source.format != "PNG" or source.width * source.height > 12_000_000:
            raise ValueError("Invalid Stage 1 source PNG")
        actual = extract_thread_geometry(source)
        if actual is None:
            # Pre-Fabric-First transparent masters are retained unchanged.
            # Do not guess or cut cloth from an unrelated untagged photograph.
            actual = source.convert("RGBA")
            if actual.getchannel("A").getextrema()[0] == 255:
                raise ValueError("Opaque source lacks verified stitch geometry")
        if size_pixels is not None:
            if not 128 <= size_pixels <= 2048:
                raise ValueError("Thread preview size out of range")
            actual.thumbnail((size_pixels, size_pixels), Image.Resampling.LANCZOS)
        output = BytesIO()
        actual.save(output, format="PNG", optimize=True)
        return output.getvalue()
