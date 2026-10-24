"""Reference-driven, deterministic six-concept embroidered lettering for CAM.

Stage 1 renders the literal quote in six distinct editorial compositions inspired
by approved GatorHats examples. Font binaries and their licenses are bundled
within the active Typography Concepts Skill from Google's official OFL archive.
No legacy font library, random font plan, or online font API is consulted.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.modules.realistic_review_ugc.stage1_stitch_texture import embroider_typography

SKILL_NAME = "gatorhats-typography-concepts"
CANVAS = (2048, 1024)
MAIN = "#2852AA"
ACCENT = "#B72B20"
FONTS = (
    "Anton-Regular.ttf", "BebasNeue-Regular.ttf", "Pacifico-Regular.ttf",
    "Lobster-Regular.ttf", "Righteous-Regular.ttf",
    "DMSerifDisplay-Regular.ttf", "AlfaSlabOne-Regular.ttf",
    "BreeSerif-Regular.ttf",
)
STYLE_NAMES = (
    "Royal Blue Block Stack", "Contrasting Condensed Stack",
    "Red Script Over Italic Blue Block", "Serif Arch With Script",
    "Large Blue Script And Small Caps", "Retro Groovy Letter Stack",
)


def _verify_fonts(directory: Path) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Stage 1 official font directory is not available")
    manifest = directory.parent / "font_sources.json"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("Stage 1 official font provenance manifest is missing")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data.get("fonts", [])
    if not isinstance(entries, list) or data.get("source_repository") != "https://github.com/google/fonts":
        raise ValueError("Stage 1 official Google Fonts provenance manifest is invalid")
    indexed = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise ValueError("Stage 1 official font provenance entry is invalid")
        name = entry["file"]
        if name in indexed:
            raise ValueError("Stage 1 official font provenance contains duplicate entries")
        indexed[name] = entry
    # A user-installed Skill may include more OFL fonts than the renderer uses.
    # Require and verify the exact files used for these six compositions; do
    # not reject a superseding, valid Google Fonts manifest merely because it
    # also catalogs other typefaces.
    missing = set(FONTS) - set(indexed)
    if missing:
        raise ValueError("Stage 1 official Google Fonts required entries are missing: " + ", ".join(sorted(missing)))
    for name in FONTS:
        font = directory / name
        entry = indexed[name]
        lic = directory.parent / entry.get("license_file", "")
        if (not font.is_file() or font.is_symlink() or not lic.is_file() or lic.is_symlink()
                or entry.get("license") != "SIL OFL 1.1"
                or "google/fonts" not in entry.get("source", "").lower()
                or not entry.get("source_url", "").startswith(
                    "https://raw.githubusercontent.com/google/fonts/main/ofl/")
                or hashlib.sha256(font.read_bytes()).hexdigest() != entry.get("sha256")
                or "SIL OPEN FONT LICENSE" not in lic.read_text(encoding="utf-8").upper()):
            raise ValueError("Stage 1 verified official font or license is missing: " + name)


def _font(directory: Path, index: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(directory / FONTS[index]), max(12, size))


def _parts(text: str, variant: int) -> tuple[str, str]:
    """Choose hierarchy by phrase, not mechanical equal-length word splitting."""
    words = text.split()
    if len(words) < 2:
        return text, ""
    if len(words) == 2:
        return words[0], words[1]
    if variant == 3:
        # The reference C3 treatment places "State of" over "MIND",
        # while "Hamburger" sits above "HELP ME".
        if len(words[-1]) <= 2:
            return words[0], " ".join(words[1:])
        return " ".join(words[:-1]), words[-1]
    if variant == 4:
        if len(words) >= 3 and words[0].upper().replace("'", "") in {"YALL", "WE", "I"}:
            return " ".join(words[:-1]), words[-1]
        return words[0], " ".join(words[1:])
    if variant == 5 and len(words) >= 3 and len(words[-1]) >= 5 and len(words[0]) <= 6:
        return " ".join(words[:-1]), words[-1]
    if len(words) == 3 and words[1].casefold() in {"of", "on", "by", "for", "to"}:
        return words[0], " ".join(words[1:])
    return words[0], " ".join(words[1:])


def _raster_line(
    text: str, directory: Path, font_index: int,
    max_width: int, max_height: int, *,
    slant: float = 0.0, tracking: int = 0, stroke: int = 0,
) -> Image.Image:
    """Rasterize only glyphs, then fit the full word(s) into a bounded region."""
    font = _font(directory, font_index, 340)
    ruler = ImageDraw.Draw(Image.new("L", (1, 1)))
    if tracking:
        advances = [int(math.ceil(ruler.textlength(letter, font=font))) for letter in text]
        width = sum(advances) + max(0, len(text) - 1) * tracking
    else:
        width = int(math.ceil(ruler.textlength(text, font=font)))
    bbox = ruler.textbbox((0, 0), text, font=font, stroke_width=stroke)
    layer = Image.new("RGBA", (max(16, width + stroke * 3 + 24),
                               max(16, bbox[3] - bbox[1] + stroke * 3 + 24)),
                      (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    at_y = 10 - bbox[1] + stroke
    if tracking:
        x = 10
        for ch, adv in zip(text, advances):
            draw.text((x, at_y), ch, font=font, fill=MAIN, stroke_width=stroke,
                      stroke_fill=MAIN)
            x += adv + tracking
    else:
        draw.text((10, at_y), text, font=font, fill=MAIN, stroke_width=stroke,
                  stroke_fill=MAIN)
    tight = layer.getbbox()
    if tight is None:
        raise ValueError("No supported glyphs in Stage 1 quote")
    layer = layer.crop(tight)
    if slant:
        shear = abs(slant)
        extra = int(round(layer.height * shear))
        a, b, c = 1.0, slant, -slant * layer.height
        layer = layer.transform((layer.width + extra, layer.height),
                                Image.Transform.AFFINE, (a, b, c, 0, 1, 0),
                                resample=Image.Resampling.BICUBIC)
        bounds = layer.getbbox()
        if bounds:
            layer = layer.crop(bounds)
    ratio = min(max_width / layer.width, max_height / layer.height)
    layer = layer.resize((max(1, round(layer.width * ratio)),
                          max(1, round(layer.height * ratio))),
                         Image.Resampling.LANCZOS)
    return layer


def _place(
    frame: Image.Image, directory: Path, font_index: int, text: str,
    *, center_y: int, max_width: int, max_height: int,
    color: str = MAIN, slant: float = 0.0, tracking: int = 0,
    stroke: int = 0, center_x: int = 1024,
) -> None:
    layer = _raster_line(text, directory, font_index, max_width, max_height,
                         slant=slant, tracking=tracking, stroke=stroke)
    # RGB is recolored without losing antialiasing: color only covers ink.
    alpha = layer.getchannel("A")
    color_layer = Image.new("RGBA", layer.size, color)
    color_layer.putalpha(alpha)
    x = center_x - layer.width // 2
    y = center_y - layer.height // 2
    frame.alpha_composite(color_layer, (x, y))


def _arched_line(
    frame: Image.Image, directory: Path, text: str, *,
    font_index: int = 5, center_y: int = 372,
    max_width: int = 1610, bow: int = 165,
) -> None:
    """Arc individual real font glyphs, keeping the top inscription legible."""
    words = text.upper()
    painter = ImageDraw.Draw(Image.new("RGBA", (2, 2)))
    preferred = 245
    font = _font(directory, font_index, preferred)
    advance = painter.textlength(words, font=font)
    font = _font(directory, font_index, max(76, int(preferred * min(1, max_width / max(1, advance)))))
    widths = [painter.textlength(ch, font=font) for ch in words]
    total = sum(widths)
    if total <= 0:
        raise ValueError("Arc inscription is empty")
    ratio = min(1, max_width / total)
    cursor = -total / 2
    for glyph, width in zip(words, widths):
        middle = cursor + width / 2
        n = middle / max(1, total / 2)
        if glyph.strip():
            em = font.size + 150
            tile = Image.new("RGBA", (em * 2, em * 2), (0, 0, 0, 0))
            dr = ImageDraw.Draw(tile)
            bb = dr.textbbox((0, 0), glyph, font=font)
            dr.text((em - (bb[2] - bb[0]) / 2 - bb[0],
                     em - (bb[3] - bb[1]) / 2 - bb[1]),
                    glyph, font=font, fill=MAIN)
            tile = tile.rotate(-18 * n, resample=Image.Resampling.BICUBIC,
                               expand=False)
            bb = tile.getbbox()
            if bb:
                tile = tile.crop(bb)
                x = round(1024 + middle * ratio - tile.width / 2)
                y = round(center_y + bow * (n * n) - tile.height / 2)
                if 0 <= x and x + tile.width <= CANVAS[0] and 0 <= y and y + tile.height <= CANVAS[1]:
                    frame.alpha_composite(tile, (x, y))
        cursor += width


def _wave_stack(frame: Image.Image, directory: Path, text: str) -> None:
    words = text.upper().split()
    if len(words) == 1:
        lines = words
    elif len(words) == 2:
        lines = words
    elif (len(words) == 3 and max(map(len, words)) <= 6
          and words[1].casefold() not in {"of", "and", "the", "in", "on", "for"}):
        lines = words
    else:
        lines = [words[0], " ".join(words[1:])]
    height = 660 // max(1, len(lines))
    positions = ([512] if len(lines) == 1 else
                 [355, 670] if len(lines) == 2 else
                 [260, 515, 760])
    for index, (line, y) in enumerate(zip(lines, positions)):
        font_index = 6 if index % 2 == 0 else 7
        _place(frame, directory, font_index, line,
               center_y=y, center_x=1024 + (34 if index % 2 else -27),
               max_width=1740 if len(lines) != 3 else 1500,
               max_height=height if len(lines) != 3 else 195,
               slant=-0.08 if index % 2 == 0 else 0.06)


def _render_one(quote: str, directory: Path, slot: int) -> Image.Image:
    frame = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    first, rest = _parts(quote, slot)
    if slot == 1:
        # Samples: CHAOS / COORDINATOR, STATE / OF MIND.
        _place(frame, directory, 0, first.upper(), center_y=327,
               max_width=1150, max_height=195)
        if rest:
            _place(frame, directory, 0, rest.upper(), center_y=623,
                   max_width=1660, max_height=265)
    elif slot == 2:
        # Taller first line and oversized lower line; strong contrast.
        _place(frame, directory, 1, first.upper(), center_y=315,
               max_width=1130, max_height=210, tracking=5)
        if rest:
            _place(frame, directory, 0, rest.upper(), center_y=628,
                   max_width=1710, max_height=275, slant=0.035)
    elif slot == 3:
        # Reference: red "State of" over large slanted blue "MIND".
        _place(frame, directory, 3, first.title(), center_y=344,
               max_width=1370, max_height=230, color=ACCENT,
               slant=0.06)
        if rest:
            _place(frame, directory, 0, rest.upper(), center_y=590,
                   max_width=1710, max_height=340, slant=0.17)
    elif slot == 4:
        # Serif arc above contrasting cursive line.
        _arched_line(frame, directory, first)
        if rest:
            _place(frame, directory, 3, rest.title(), center_y=673,
                   max_width=1250, max_height=195)
    elif slot == 5:
        # Loose flowing script headline and a compact upright cap line.
        _place(frame, directory, 2, first.title(), center_y=378,
               max_width=1570, max_height=365, slant=0.03)
        if rest:
            _place(frame, directory, 1, rest.upper(), center_y=672,
                   max_width=1220, max_height=176, tracking=3, stroke=1)
    elif slot == 6:
        # Wide, chunky, staggered retro serif.
        _wave_stack(frame, directory, quote)
    else:
        raise ValueError("Unknown Stage 1 typography concept")
    if frame.getbbox() is None:
        raise ValueError("Empty typography concept output")
    return frame


def render_six(quote: str, font_dir: Path, workspace: Path) -> tuple[Path, ...]:
    """Produce six unique transparent stitched masters with safe crash recovery."""
    quote = " ".join(str(quote or "").strip().split())
    if not quote or len(quote) > 55 or any(ord(c) < 32 for c in quote):
        raise ValueError("Stage 1 quote must contain 1–55 printable characters")
    _verify_fonts(font_dir)
    target = workspace / "output" / "final"
    target.mkdir(parents=True, exist_ok=True)
    names = {f"design_{number:02}.png" for number in range(1, 7)}
    if any(p.name not in names or p.is_symlink() or not p.is_file() for p in target.iterdir()):
        raise ValueError("Stage 1 output workspace contains stale final files")
    outputs = []
    hashes = set()
    for concept in range(1, 7):
        image = embroider_typography(_render_one(quote, font_dir, concept), concept=concept)
        path = target / f"design_{concept:02}.png"
        if path.is_symlink():
            raise ValueError("Stage 1 checkpoint may not be a symlink")
        reusable = False
        if path.exists():
            try:
                with Image.open(path) as previous:
                    previous.load()
                    reusable = (previous.format == "PNG" and previous.mode == "RGBA"
                                and previous.size == CANVAS
                                and previous.tobytes() == image.tobytes())
            except (OSError, ValueError):
                reusable = False
            if not reusable:
                # Skill updates can change a deterministic renderer while a
                # failed job still owns old local checkpoints. Preserve the
                # stale bytes for diagnosis and atomically regenerate only the
                # affected local final; published versions are untouched.
                stale = workspace / "output" / ".stale-checkpoints" / hashlib.sha256(path.read_bytes()).hexdigest()
                stale.mkdir(parents=True, exist_ok=True)
                path.replace(stale / path.name)
        if not reusable:
            image.save(path, format="PNG", optimize=True)
        with Image.open(path) as verified:
            verified.load()
            if (verified.format != "PNG" or verified.mode != "RGBA"
                or verified.size != CANVAS or verified.getbbox() is None
                or verified.getpixel((0, 0))[3] != 0):
                raise ValueError("Stage 1 design failed PNG validation")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in hashes:
            raise ValueError("Two Stage 1 concepts produced the same design")
        hashes.add(digest)
        outputs.append(path)
    (workspace / "output" / "manifest.json").write_text(
        json.dumps({"skill": SKILL_NAME, "quote": quote, "count": 6,
                    "concepts": STYLE_NAMES, "finish": "reference_satin_v4",
                    "source": "google_fonts_ofl_bundled",
                    "sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs]}),
        encoding="utf-8",
    )
    return tuple(outputs)