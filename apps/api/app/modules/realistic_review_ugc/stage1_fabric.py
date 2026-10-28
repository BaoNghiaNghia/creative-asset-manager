"""Compatibility helpers for pre-existing Fabric-First PNGs.

Stage 1's canonical design is an alpha-preserving PNG again. Keep decoding of
historical opaque Fabric-First photos, whose embedded source geometry is still
used for on-cap placement. Fabric renders in this module are material-only
previews and must NEVER become the primary design or be uploaded as a master.
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


def _flat_white_artwork_geometry(source: Image.Image) -> Image.Image | None:
    """Recover only a provably plain white design canvas, never a fabric photo.

    Some uploaded Stage 1 skills produce blue/dark typography on white without
    the historical alpha/metadata. This temporary cap-preview matte does NOT
    alter the master PNG or its download. An opaque photo or a nonwhite paper
    texture must still fail closed rather than stamp a rectangle on the cap.
    """
    rgb = source.convert("RGB")
    # Sample the complete outside border at reduced resolution. A true flat
    # canvas has almost exclusively white/neutral corners AND edges.
    probe = rgb.resize((96, 96), Image.Resampling.BILINEAR)
    samples = [
        probe.getpixel((x, y))
        for y in range(96) for x in range(96)
        if x < 5 or x >= 91 or y < 5 or y >= 91
    ]
    white = sum(min(pixel) >= 243 and max(pixel) - min(pixel) <= 9 for pixel in samples)
    if white / len(samples) < .985:
        return None

    # Use color-channel distance from pure white, retaining real colored
    # lettering and soft AA edges while dropping only the flat background.
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white"))
    red, green, blue = diff.split()
    distance = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    alpha = distance.point([
        0 if value <= 8 else 255 if value >= 28
        else round((value - 8) * 255 / 20) for value in range(256)
    ])
    coverage = sum(alpha.histogram()[24:]) / max(1, rgb.width * rgb.height)
    # Reject an all-white image and giant nonwhite photo rectangles. Both
    # cannot be safely interpreted as an isolated embroidery design.
    if not .001 <= coverage <= .48:
        return None
    rgba = rgb.convert("RGBA")
    rgba.putalpha(alpha)
    return rgba


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
                actual = _flat_white_artwork_geometry(source)
                if actual is None:
                    raise ValueError("Opaque source lacks verified stitch geometry")
        if size_pixels is not None:
            if not 128 <= size_pixels <= 2048:
                raise ValueError("Thread preview size out of range")
            actual.thumbnail((size_pixels, size_pixels), Image.Resampling.LANCZOS)
        output = BytesIO()
        actual.save(output, format="PNG", optimize=True)
        return output.getvalue()