"""Conservative Stage 1 typography matte repair.

Only remove a uniformly near-black, opaque background that connects to the
canvas border. Other pictures (including real black hat material, existing
transparency and colored backgrounds) are kept byte-for-byte. Published Drive
masters are never rewritten by the preview helpers.
"""
from __future__ import annotations

from collections import deque
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_PREVIEW_PIXELS = 12_000_000
STAGE1_BLACK_MATTE_SKILLS = frozenset({
    "gatorhats-typography-concepts", "gatorhats-stage1-six-designs",
})


def black_matte_to_transparency(original: Image.Image) -> Image.Image | None:
    """Return a new RGBA image when a substantial black border matte is found."""
    width, height = original.size
    if width < 16 or height < 16 or width * height > MAX_PREVIEW_PIXELS:
        return None
    image = original.convert("RGBA")
    pixels = image.load()
    # A partially cropped glyph may touch an edge, but at least 85% of the
    # border and all four corners must still be black before removing any
    # pixels. Inspect the FULL border; sparse sampling misses dark-photo edges.
    border = (
        [pixels[x, 0] for x in range(width)]
        + [pixels[x, height - 1] for x in range(width)]
        + [pixels[0, y] for y in range(height)]
        + [pixels[width - 1, y] for y in range(height)]
    )
    is_matte = lambda p: p[3] >= 250 and max(p[:3]) <= 22
    if (not all(is_matte(pixels[x, y]) for x, y in (
        (0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1),
    )) or sum(is_matte(p) for p in border) < int(len(border) * 0.85)):
        return None
    seen = bytearray(width * height)
    queue = deque()
    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))
    matte = Image.new("L", (width, height))
    mask = matte.load()
    count = 0
    while queue:
        x, y = queue.popleft()
        index = y * width + x
        if seen[index]:
            continue
        seen[index] = 1
        r, g, b, a = pixels[x, y]
        if a < 245 or max(r, g, b) > 30:
            continue
        count += 1
        mask[x, y] = 255
        if x:
            queue.append((x - 1, y))
        if x + 1 < width:
            queue.append((x + 1, y))
        if y:
            queue.append((x, y - 1))
        if y + 1 < height:
            queue.append((x, y + 1))
    if count < width * height // 12 or count == width * height:
        return None
    alpha = image.getchannel("A")
    alpha.paste(0, mask=matte)
    image.putalpha(alpha)
    return image


def strip_black_matte_to_transparency(path: str | Path) -> bool:
    """Modify only a disposable, unuploaded local PNG output checkpoint."""
    path = Path(path)
    with Image.open(path) as original:
        repaired = black_matte_to_transparency(original)
        if repaired is None:
            return False
        repaired.save(path, "PNG")
        return True


def stage1_artwork_preview_bytes(content: bytes) -> tuple[bytes, bool]:
    """Return transparent PNG if matte-like, else preserve original bytes.

    Used for already-published Stage 1 images, without changing remote IDs or
    touching the originals. Bounding the input avoids image-decompression DoS
    if a storage provider supplies unexpected content.
    """
    if not content or len(content) > MAX_PREVIEW_BYTES:
        return content, False
    try:
        with Image.open(BytesIO(content)) as original:
            repaired = black_matte_to_transparency(original)
            if repaired is None:
                return content, False
            output = BytesIO()
            repaired.save(output, format="PNG", optimize=True)
            return output.getvalue(), True
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return content, False
