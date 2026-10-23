"""Deterministic six-PNG CAM Stage 1 typography adapter.

Trust boundary: this renderer is signed application code, not executable Python
loaded from arbitrary uploaded Skills. Font names are allow-listed OFL assets
packaged from CAM's vetted Stage 1 font registry. No image-generation provider
or network access is needed. Each run writes into its own processing workspace.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.modules.realistic_review_ugc.stage1_stitch_texture import embroider_typography

SKILL_NAME = "gatorhats-typography-concepts"
CANVAS = (2048, 1024)
MAIN = "#3558B5"
ACCENT = "#AC211C"
FONTS = ("BebasNeue-Regular.ttf", "SimplePrint.ttf", "Pacifico-Regular.ttf",
         "Lobster-Regular.otf", "Signwood.ttf", "dafont_classica_3.ttf",
         "AireExterior.ttf", "dafont_yeseva_one.ttf")
STYLE_NAMES = ("Varsity Stack", "Block Stack", "Script Lockup 2-Color",
               "Club Arch", "Script Overlap", "Groovy Wavy Stack")


def _font(directory: Path, number: int, size: int) -> ImageFont.FreeTypeFont:
    path = directory / FONTS[number]
    if not path.is_file() or path.is_symlink():
        raise ValueError("Stage 1 typography font missing or unsafe: " + path.name)
    return ImageFont.truetype(str(path), max(10, size))


def _split(phrase: str, count: int) -> list[str]:
    words = phrase.split()
    if len(words) <= count:
        return words
    # Deterministic, meaning-preserving line grouping. Never alter glyph text.
    out = []
    start = 0
    for n in range(count):
        rest = len(words) - start
        slots = count - n
        take = max(1, math.ceil(rest / slots))
        out.append(" ".join(words[start:start + take]))
        start += take
    return out


def _fit_font(draw: ImageDraw.ImageDraw, font_dir: Path, font_index: int,
              text: str, width: int, max_height: int, preferred: int) -> ImageFont.FreeTypeFont:
    for size in range(preferred, 15, -2):
        f = _font(font_dir, font_index, size)
        bbox = draw.textbbox((0, 0), text, font=f, stroke_width=0)
        if bbox[2] - bbox[0] <= width and bbox[3] - bbox[1] <= max_height:
            return f
    raise ValueError("Quote is too long to fit an embroidery-safe typography layout.")


def _center_text(canvas: Image.Image, font_dir: Path, font_index: int, text: str,
                 y: int, *, color: str = MAIN, preferred: int = 265,
                 max_width: int = 1720, max_height: int = 350,
                 offset_x: int = 0) -> None:
    layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    f = _fit_font(draw, font_dir, font_index, text, max_width, max_height, preferred)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=f)
    x = (CANVAS[0] - (right - left)) // 2 - left + offset_x
    draw.text((x, y - top), text, font=f, fill=color)
    canvas.alpha_composite(layer)


def _meaning_parts(quote: str) -> tuple[str, str]:
    """Preserve phrase order and keep a preposition with its following word.

    Examples: State / of Mind, Chaos / Coordinator, Hamburger / Help me.
    """
    words = quote.split()
    if len(words) <= 1:
        return quote, ""
    if len(words) >= 3 and words[1].lower() in (
        "of", "on", "in", "at", "for", "with", "by", "to",
    ):
        return words[0], " ".join(words[1:])
    if len(words) >= 3:
        return " ".join(words[:1]), " ".join(words[1:])
    return words[0], words[1]


def _arch(canvas: Image.Image, font_dir: Path, quote: str) -> None:
    words = quote.upper()
    draw = ImageDraw.Draw(canvas)
    font = _fit_font(draw, font_dir, 5, words, 1470, 270, 230)
    full_width = draw.textlength(words, font=font)
    if full_width <= 0:
        raise ValueError("No text to render")
    cursor = -full_width / 2
    for char in words:
        advance = draw.textlength(char, font=font)
        mid = cursor + advance / 2
        theta = mid / max(full_width / 2, 1) * 0.53
        glyph = Image.new("RGBA", (450, 460), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glyph)
        box = gd.textbbox((0, 0), char, font=font)
        gd.text((225 - (box[2] - box[0]) / 2 - box[0],
                 230 - (box[3] - box[1]) / 2 - box[1]), char, fill=MAIN, font=font)
        glyph = glyph.rotate(-math.degrees(theta), resample=Image.Resampling.BICUBIC,
                             expand=False)
        cx = CANVAS[0] / 2 + (mid / full_width) * 1470
        cy = 570 - 330 * (1 - (mid / max(full_width / 2, 1)) ** 2)
        canvas.alpha_composite(glyph, (round(cx - 225), round(cy - 230)))
        cursor += advance


def _render_one(quote: str, font_dir: Path, slot: int) -> Image.Image:
    image = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    headline, supporting = _meaning_parts(quote)
    words = _split(quote, 2)
    bottom = (words[1] if len(words) > 1 else words[0]).upper()
    if slot == 1:  # varsity/stacked, preserving a single-word quote
        lines = [quote.upper()] if len(quote.split()) <= 2 else _split(quote.upper(), 2)
        for n, line in enumerate(lines):
            _center_text(image, font_dir, 0, line, 380 + n * 235,
                         preferred=290 if len(lines) == 1 else 210)
    elif slot == 2:  # dense block, alternate approved face
        lines = _split(quote.upper(), 2)
        for n, line in enumerate(lines):
            _center_text(image, font_dir, 1, line, 325 + n * 250,
                         preferred=320 if len(lines) == 1 else 240)
    elif slot == 3:  # sample C3: red script headline, large blue key line
        if supporting:
            _center_text(image, font_dir, 3, headline.title(), 286,
                         color=ACCENT, preferred=258, max_width=1660)
            _center_text(image, font_dir, 0, supporting.upper(), 535,
                         color=MAIN, preferred=306, max_width=1710,
                         max_height=305)
        else:
            _center_text(image, font_dir, 3, headline.title(), 385,
                         color=ACCENT, preferred=290)
    elif slot == 4:  # sample C4: arched headline with script nested below
        _arch(image, font_dir, headline)
        if supporting:
            _center_text(image, font_dir, 3, supporting.title(), 585,
                         preferred=172, max_width=1170, max_height=195)
    elif slot == 5:  # sample C5: prominent script + compact caps underlay
        _center_text(image, font_dir, 2, headline.title(), 305,
                     preferred=292, max_width=1690)
        if supporting:
            _center_text(image, font_dir, 0, supporting.upper(), 610,
                         preferred=160, max_width=1430, max_height=195)
    else:  # groovy/wavy stack, no added symbols
        lines = _split(quote.upper(), 2)
        if len(lines) == 1:
            _center_text(image, font_dir, 6, lines[0], 400, preferred=280)
        else:
            _center_text(image, font_dir, 6, lines[0], 285, preferred=235, offset_x=-40)
            _center_text(image, font_dir, 7, lines[1], 585, preferred=235, offset_x=35)
    if image.getbbox() is None:
        raise ValueError("Empty typography output")
    return image


def render_six(quote: str, font_dir: Path, workspace: Path) -> tuple[Path, ...]:
    """Return six unique PNG files or fail before upload; never draw a hat."""
    quote = " ".join(str(quote or "").strip().split())
    if not quote or len(quote) > 150 or any(ord(c) < 32 for c in quote):
        raise ValueError("Stage 1 quote must contain 1-150 printable characters")
    if len(quote) > 55:
        raise ValueError("Typography quote exceeds 55 characters; shorten the wording.")
    if not font_dir.is_dir() or font_dir.is_symlink():
        raise ValueError("Typography Skill font assets are unavailable")
    target = workspace / "output" / "final"
    target.mkdir(parents=True, exist_ok=True)
    # Crash recovery: permit only canonical earlier numbered finals and
    # verify their complete RGBA pixels match this deterministic renderer.
    # Never overwrite or regenerate a preserved good checkpoint.
    valid_names = {f"design_{index:02}.png" for index in range(1, 7)}
    if any(item.name not in valid_names or item.is_symlink() or not item.is_file()
           for item in target.iterdir()):
        raise ValueError("Stage 1 output workspace contains stale final files")
    outputs: list[Path] = []
    digests: set[str] = set()
    for slot in range(1, 7):
        # Render actual thread grooves and raised edges without altering
        # the vector glyph outline or the transparent background.
        image = embroider_typography(_render_one(quote, font_dir, slot), concept=slot)
        path = target / f"design_{slot:02}.png"
        if path.exists():
            with Image.open(path) as checkpoint:
                checkpoint.load()
                if (checkpoint.mode != "RGBA" or checkpoint.size != CANVAS
                        or checkpoint.format != "PNG" or checkpoint.getbbox() is None
                        or checkpoint.tobytes() != image.tobytes()):
                    raise ValueError("Existing Stage 1 checkpoint changed or corrupt")
        else:
            image.save(path, "PNG", optimize=True)
        with Image.open(path) as verified:
            verified.load()
            if (verified.mode != "RGBA" or verified.size != CANVAS
                    or verified.format != "PNG" or verified.getbbox() is None):
                raise ValueError("Stage 1 design failed PNG validation")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in digests:
            raise ValueError("Two typography slots produced identical artwork")
        digests.add(digest)
        outputs.append(path)
    (workspace / "output" / "manifest.json").write_text(
        json.dumps({"skill": SKILL_NAME, "quote": quote, "count": 6,
                    "concepts": STYLE_NAMES, "finish": "directional_satin_thread_v2",
                    "sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs]}),
        encoding="utf-8")
    return tuple(outputs)
