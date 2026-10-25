"""Conservative processing of black-matted artwork, never product photographs."""
from collections import deque
from pathlib import Path

from PIL import Image


def strip_black_matte_to_transparency(path: str | Path) -> bool:
    """Remove only a uniformly dark, opaque, border-connected background."""
    path = Path(path)
    with Image.open(path) as original:
        image = original.convert("RGBA")
        width, height = image.size
        if width < 16 or height < 16:
            return False
        pixels = image.load()
        border = [
            *[pixels[x, 0] for x in range(0, width, max(1, width // 24))],
            *[pixels[x, height - 1] for x in range(0, width, max(1, width // 24))],
            *[pixels[0, y] for y in range(0, height, max(1, height // 24))],
            *[pixels[width - 1, y] for y in range(0, height, max(1, height // 24))],
        ]
        if any(a < 250 or max(r, g, b) > 22 for r, g, b, a in border):
            return False
        seen = bytearray(width * height)
        queue = deque()
        for x in range(width):
            queue.append((x, 0))
            queue.append((x, height - 1))
        for y in range(height):
            queue.append((0, y))
            queue.append((width - 1, y))
        matte = Image.new("L", (width, height))
        output = matte.load()
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
            output[x, y] = 255
            count += 1
            if x:
                queue.append((x - 1, y))
            if x + 1 < width:
                queue.append((x + 1, y))
            if y:
                queue.append((x, y - 1))
            if y + 1 < height:
                queue.append((x, y + 1))
        if count < width * height // 12:
            return False
        alpha = image.getchannel("A")
        alpha.paste(0, mask=matte)
        image.putalpha(alpha)
        image.save(path, "PNG")
        return True
