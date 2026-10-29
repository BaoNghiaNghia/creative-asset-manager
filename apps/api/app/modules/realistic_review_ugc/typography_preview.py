"""G4 MVP: deterministic balanced SVG concept previews from reviewed Style DNA.

Pure function: no database writes, providers, files or job creation.
The source keyword remains byte-for-byte identical as Unicode text; only
layout/typography relationships are transferred, never reference artwork.
Preview SVGs are transparent and are NOT eligible Stage 1 deliverables.
"""
from __future__ import annotations

from html import escape
import re
import unicodedata
from typing import Any

from app.modules.realistic_review_ugc.typography_dna_contract import (
    TypographyDnaContractError, validate_typography_dna,
)

PREVIEW_VERSION = "g4-balanced-svg-mvp-v1"
VIEW_WIDTH, VIEW_HEIGHT = 960, 560
MAX_KEYWORD_CHARS = 160
# Names are from the existing Stage 1 Google Fonts OFL allowlist. The SVG
# retains system fallbacks until the bundled OFL fonts are loaded by the client;
# it never fetches fonts from third-party URLs.
_SAFE_FONTS = {
    "serif": ("DM Serif Display", "serif"),
    "slab_serif": ("Bree Serif", "serif"),
    "script": ("Pacifico", "cursive"),
    "display": ("Righteous", "sans-serif"),
    "sans_serif": ("Anton", "sans-serif"),
    "handwritten": ("Lobster", "cursive"),
    "monospace": ("Bebas Neue", "sans-serif"),
}
_LAYOUTS = ("arched", "center_stack", "offset_stack")


class TypographyPreviewError(ValueError):
    """Input not eligible for an exact-text, structural preview."""


def _validate_keyword(keyword: str) -> None:
    if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > MAX_KEYWORD_CHARS:
        raise TypographyPreviewError("Keyword is empty or exceeds preview limit.")
    # Preserve source text verbatim; refuse invisible direction controls and
    # unpaired surrogates rather than silently normalizing or deleting them.
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in keyword):
        raise TypographyPreviewError("Keyword contains unsupported invisible controls.")
    if any(char not in " " and char.isspace() for char in keyword):
        raise TypographyPreviewError("Only ordinary spaces are supported between words.")
    if len(keyword.split()) > 30:
        raise TypographyPreviewError("Keyword has too many words for a vector preview.")


def _groups(keyword: str, desired_lines: int) -> list[str]:
    """Partition at complete word boundaries, preserving ALL original spaces."""
    chunks = re.findall(r"[^ ]+ *| +", keyword)
    if not chunks or "".join(chunks) != keyword:
        raise TypographyPreviewError("Keyword cannot be safely segmented.")
    count = min(desired_lines, len(chunks))
    if count == 1:
        return [keyword]
    groups: list[str] = []
    index = 0
    for line in range(count):
        remaining_lines = count - line
        if remaining_lines == 1:
            groups.append("".join(chunks[index:]))
            break
        max_end = len(chunks) - (remaining_lines - 1)
        total_remaining = sum(len(part) for part in chunks[index:])
        target = total_remaining / remaining_lines
        end = index + 1
        while end < max_end and sum(len(part) for part in chunks[index:end + 1]) <= target:
            end += 1
        groups.append("".join(chunks[index:end]))
        index = end
    if "".join(groups) != keyword:
        raise TypographyPreviewError("Exact source keyword was not preserved.")
    return groups


def _font(dna: dict[str, Any], variant: int) -> tuple[str, str]:
    mood = dna["font_mood"].get("primary", "sans_serif")
    base, fallback = _SAFE_FONTS.get(mood, _SAFE_FONTS["sans_serif"])
    # Change the font family by one safe step rather than tracing the exact
    # glyph silhouette of the source.
    if variant == 1:
        base, fallback = ("Anton", "sans-serif") if fallback == "serif" else ("DM Serif Display", "serif")
    return base, fallback


def _size(text: str, wanted: float, *, wide: bool = False) -> int:
    # Conservative estimate; textLength further bounds measured SVG text.
    # Vietnamese combining accents and fullwidth glyphs are not dropped.
    width_units = sum(1.75 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1 for ch in text)
    maximum = 860 / max(1, width_units * (.70 if wide else .61))
    return max(12, int(min(wanted, maximum)))


def _svg_line(
    text: str, *, x: int, y: int, font: tuple[str, str],
    font_size: int, weight: int, anchor: str, rotation: int = 0,
    arch: bool = False,
) -> str:
    escaped = escape(text, quote=False)
    family = f"'{font[0]}',{font[1]}"
    # Use unique local fragment IDs only. No external URL/CSS/font load,
    # foreignObject, animation or scripting is emitted.
    length = max(1, len(text))
    estimated = length * font_size * .64
    bound = min(900, max(64, estimated))
    layout = f' textLength="{bound:.1f}" lengthAdjust="spacingAndGlyphs"' if estimated > 900 else ""
    if arch:
        return (
            '<defs><path id="g4-arc" d="M 100 320 Q 480 92 860 320"/></defs>'
            f'<text xml:space="preserve" font-family="{family}" font-size="{font_size}" '
            f'font-weight="{weight}" fill="#20344d" text-anchor="middle"{layout}>'
            f'<textPath href="#g4-arc" startOffset="50%">{escaped}</textPath></text>'
        )
    transform = f' transform="rotate({rotation} {x} {y})"' if rotation else ""
    return (
        f'<text xml:space="preserve" x="{x}" y="{y}" '
        f'font-family="{family}" font-size="{font_size}" font-weight="{weight}" '
        f'fill="#20344d" text-anchor="{anchor}"{layout}{transform}>{escaped}</text>'
    )


def build_typography_previews(
    keyword: str, *, dna: dict[str, Any], reference_id: str, dna_id: str,
    count: int = 3,
) -> list[dict[str, Any]]:
    """Build 1-3 unique vector previews; no generation or publication."""
    _validate_keyword(keyword)
    if type(count) is not int or not 1 <= count <= 3:
        raise TypographyPreviewError("Preview count must be between one and three.")
    if not reference_id or not dna_id:
        raise TypographyPreviewError("Approved reference and DNA are required.")
    try:
        validated = validate_typography_dna(dna, source_sha256=dna["image_hash"])
    except (KeyError, TypographyDnaContractError) as exc:
        raise TypographyPreviewError("Style DNA failed validation.") from exc
    nodes = validated["layout_graph"]["nodes"]
    primary = next((item for item in nodes if item["role"] == "primary"), nodes[0])
    prefers_arch = primary["path_type"] in {"arch", "curve"}
    weight = 720 if validated["font_mood"]["weight"] in {"bold", "heavy"} else 600
    variants: list[dict[str, Any]] = []
    for idx, layout in enumerate(_LAYOUTS[:count]):
        groups = _groups(keyword, (1, 2, 3)[idx])
        font = _font(validated, idx)
        if idx == 0:
            arch = prefers_arch and len(keyword) <= 48
            line = _svg_line(
                keyword, x=480, y=305, font=font, font_size=_size(keyword, 78),
                weight=weight, anchor="middle", arch=arch,
            )
            if not arch:
                # Variation 0 is a readable, centered full-source baseline.
                line = _svg_line(keyword, x=480, y=300, font=font, font_size=_size(keyword, 78),
                                 weight=weight, anchor="middle")
            elements = [line]
        else:
            elements = []
            spacing = 115 if idx == 1 else 92
            first_y = 295 - int(spacing * (len(groups) - 1) / 2)
            for j, group in enumerate(groups):
                ratio = max(.35, min(1.7, float(primary["size_ratio"])))
                desired = (89 if j == 0 else 59) * (1.02 if idx == 1 else .92) * ratio
                font_size = _size(group, desired, wide=idx == 2)
                x = 480 if idx == 1 else (420 if j % 2 == 0 else 540)
                elements.append(_svg_line(
                    group, x=x, y=first_y + j * spacing, font=font,
                    font_size=font_size, weight=weight if j == 0 else 600,
                    anchor="middle", rotation=0 if idx == 1 else (-4 if j % 2 == 0 else 4),
                ))
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW_WIDTH} {VIEW_HEIGHT}" '
            f'width="{VIEW_WIDTH}" height="{VIEW_HEIGHT}" role="img">'
            + "".join(elements) + "</svg>"
        )
        variants.append({
            "id": f"balanced-{layout}-v1",
            "preview_version": PREVIEW_VERSION,
            "source_keyword_exact": keyword,
            "reference_id": reference_id,
            "dna_id": dna_id,
            "layout": layout,
            "font_family": font[0],
            "influence": "balanced",
            "style_components": ["layout", "font_mood", "overlap"],
            "svg": svg,
            "transparent_background": True,
            "preview_only": True,
            "eligible_for_generation": False,
            "qa": {"exact_keyword": True, "status": "preview_only", "requires_human_review": True},
        })
    return variants
