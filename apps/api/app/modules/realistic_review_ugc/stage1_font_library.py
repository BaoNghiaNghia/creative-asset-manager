"""Approved DaFont typography references bundled with Stage 1's image skill.

Font files are from the original DaFont archives. Their license notices stay
beside the binaries in the managed skill directory. This is a font *source*
plan, not an assertion that a generative model preserved every glyph.
"""
from __future__ import annotations

from pathlib import Path

SKILL_NAME = "gatorhats-stage1-six-designs"
FONT_LIBRARY = (
    {"font": "Bebas Neue", "style": "condensed", "layout": "Bold headline, stacked hierarchy",
     "file": "BebasNeue-Regular.ttf", "url": "https://www.dafont.com/bebas-neue.font",
     "license": "SIL OFL 1.1", "group": "sans"},
    {"font": "Bebas Neue", "style": "condensed", "layout": "Tall condensed, two-line center",
     "file": "BebasNeue-Regular.ttf", "url": "https://www.dafont.com/bebas-neue.font",
     "license": "SIL OFL 1.1", "group": "sans"},
    {"font": "Pacifico", "style": "script", "layout": "Flowing script, centered balanced",
     "file": "Pacifico-Regular.ttf", "url": "https://www.dafont.com/pacifico.font",
     "license": "SIL OFL 1.1", "group": "script"},
    {"font": "Lobster", "style": "script", "layout": "Strong vintage script, varied line widths",
     "file": "Lobster-Regular.otf", "url": "https://www.dafont.com/lobster.font",
     "license": "SIL OFL 1.1", "group": "script"},
    {"font": "Bebas Neue", "style": "modern", "layout": "Compact word blocks, tight tracking",
     "file": "BebasNeue-Regular.ttf", "url": "https://www.dafont.com/bebas-neue.font",
     "license": "SIL OFL 1.1", "group": "sans"},
    {"font": "Lobster", "style": "retro", "layout": "Vintage typography, asymmetric word scale",
     "file": "Lobster-Regular.otf", "url": "https://www.dafont.com/lobster.font",
     "license": "SIL OFL 1.1", "group": "script"},
)
FONTS_DIR = Path(__file__).resolve().parents[3] / "managed_skills" / SKILL_NAME / "fonts"


def font_info_for_number(number: int, *, font_dir: Path | None = None) -> dict:
    """Return validated font source metadata for design_01..design_06."""
    if not 1 <= number <= 6:
        raise ValueError("Stage 1 design index must be 1..6")
    entry = FONT_LIBRARY[number - 1]
    base = font_dir or FONTS_DIR
    asset = base / entry["file"]
    if not asset.is_file():
        raise FileNotFoundError("Bundled Stage 1 font missing: " + entry["file"])
    return {
        "font_name": entry["font"], "font_style": entry["style"],
        "layout_name": entry["layout"], "font_source": "DaFont",
        "font_url": entry["url"], "font_license": entry["license"],
        "font_file": entry["file"],
        "font_status": "layout_reference",
    }




def render_font_guides(workspace: Path, quote: str, *, font_dir: Path | None = None) -> Path:
    """Rasterize REAL bundled DaFont glyph outlines into non-output references.

    Guides are kept under working/font-guides, outside the six output/final PNGs.
    The image model may texture the same shapes as natural brown embroidery.
    """
    from PIL import Image, ImageDraw, ImageFont

    if not quote or len(quote) > 500:
        raise ValueError("Stage 1 quote must be 1..500 characters")
    base = font_dir or FONTS_DIR
    target = workspace / "working" / "font-guides"
    target.mkdir(parents=True, exist_ok=True)
    words = quote.split()
    for number, entry in enumerate(FONT_LIBRARY, start=1):
        font_file = base / entry["file"]
        if not font_file.is_file():
            raise FileNotFoundError("Font library is missing " + entry["file"])
        # Arrange whole words into a compact, intentional typography block.
        columns = 2 if number in (1, 3, 6) else 3
        lines = []
        if len(words) <= 2:
            lines = words if number in (2, 4, 5) else [" ".join(words)]
        else:
            groups = min(columns, len(words))
            # Partition into contiguous word runs, retaining literal order.
            lines = [
                " ".join(words[i * len(words) // groups : (i + 1) * len(words) // groups])
                for i in range(groups)
            ]
        if number in (1, 4) and len(words) > 2:
            # A strong title line above smaller bottom lines.
            lines = [words[0], " ".join(words[1:])]
        width, height = 1600, 1150
        margin = 125
        font_size = 320
        while font_size >= 64:
            font = ImageFont.truetype(str(font_file), font_size)
            probe = ImageDraw.Draw(Image.new("L", (width, height)))
            measures = [probe.textbbox((0, 0), line, font=font) for line in lines]
            widths = [bounds[2] - bounds[0] for bounds in measures]
            tallest = max(bounds[3] - bounds[1] for bounds in measures)
            gap = int(font_size * .08)
            if max(widths) <= width - 2 * margin and len(lines) * (tallest + gap) <= height - 2 * margin:
                break
            font_size -= 8
        mask = Image.new("L", (width, height))
        draw = ImageDraw.Draw(mask)
        line_height = tallest + int(font_size * .08)
        top = (height - line_height * len(lines)) // 2
        for i, line in enumerate(lines):
            bounds = draw.textbbox((0, 0), line, font=font)
            x = (width - (bounds[2] - bounds[0])) // 2 - bounds[0]
            y = top + i * line_height - bounds[1]
            draw.text((x, y), line, font=font, fill=255)
        # One thread pigment (#6b3f28), subtle same-hue satin sheen only.
        pigment = Image.new("RGBA", (width, height), (107, 63, 40, 255))
        pigment_draw = ImageDraw.Draw(pigment)
        for diag in range(-height, width + height, 9):
            pigment_draw.line([(diag, 0), (diag + height, height)], fill=(116, 71, 48, 255), width=2)
        pigment.putalpha(mask)
        pigment.save(target / f"design_{number:02}.png", "PNG")
    return target

def font_brief() -> str:
    lines = []
    for index, entry in enumerate(FONT_LIBRARY, start=1):
        lines.append(
            f"design_{index:02}.png: {entry['font']} from DaFont; "
            f"FONT FILE fonts/{entry['file']}; "
            f"LAYOUT {entry['layout']}; "
            f"LICENSE {entry['license']}."
        )
    return " ".join(lines)
