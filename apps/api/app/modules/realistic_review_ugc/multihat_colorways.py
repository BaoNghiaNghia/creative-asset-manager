"""Verified Valucap 8869 multi-color palettes for GatorHats Products Skill 5.

Each independent shot has its own distinct cap colors, drawn from the approved
13-color inventory. The queue persists the choices and hashes of their official
front stock; retries never choose new colors, and workers reject changed stock.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import random
from typing import Mapping, Sequence
from uuid import uuid4

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from app.modules.image_generation.providers import ReferenceImageInput
from app.modules.realistic_review_ugc.colorways import COLORS, ColorwayError, _stock_bytes
from app.modules.realistic_review_ugc.generation_handler import _prepared_image

# Match the intended 05 / 06 / 07 compositions (4, 4, 3 hats).
STAGE_CAP_COUNTS = {
    "prod_s2_mh_1": 4,
    "prod_s2_mh_2": 4,
    "prod_s2_mh_3": 3,
}
COLOR_NAMES = dict(COLORS)
# Distinct official colors can be visually similar. Favor four visibly
# different visor families per four-hat photo, and warm/blue/green for 3 hats.
COLOR_FAMILIES = {
    "khaki-maroon": "warm",
    "natural-black": "neutral",
    "natural-brown": "warm",
    "natural-camo-green": "green",
    "natural-charcoal": "neutral",
    "natural-forest-green": "green",
    "natural-khaki": "neutral",
    "natural-maroon": "warm",
    "natural-mossy-oak-breakup": "green",
    "natural-navy": "blue",
    "natural-realtree-all-purpose": "green",
    "natural-red": "warm",
    "natural-royal": "blue",
}
MAX_STOCK_PIXELS = 36_000_000


class MultiHatColorwayError(ValueError):
    """Unavailable / invalid trusted official stock palette."""


def validated_palette(stage: str, value: object) -> list[str]:
    """Return a valid pinned per-shot palette or an empty list.

    All caps in one output must have different officially approved colorways.
    Empty means missing/legacy, not a request to invent free-form hat colors.
    """
    count = STAGE_CAP_COUNTS.get(stage)
    if count is None or not isinstance(value, (tuple, list)) or len(value) != count:
        return []
    if any(not isinstance(key, str) or key not in COLOR_NAMES for key in value):
        return []
    if len(set(value)) != len(value):
        return []
    return list(value)


def available_stock_colors(settings) -> list[str]:
    """Only verified front photos from approved 8869 inventory are candidates."""
    available = []
    for key in COLOR_NAMES:
        try:
            _stock_bytes(settings, key)
        except (OSError, ValueError, ColorwayError):
            continue
        available.append(key)
    return available


def choose_palettes(
    *, tenant_id: str, source_plan_id: str, source_revision: str,
    available: Sequence[str], pinned: Mapping[str, Sequence[str]] | None = None,
    run_key: str | None = None,
) -> dict[str, list[str]]:
    """Fill the missing slots; respect already pinned stages.

    A unique random key is bound to each design run. Prioritize visibly distinct
    families within each photo (warm, blue, green, neutral), then prefer colors
    not used by other shots. Repeats between photos are acceptable; repeats
    within a photo are never allowed.
    """
    choices = sorted(set(available) & COLOR_NAMES.keys())
    if len(choices) < max(STAGE_CAP_COUNTS.values()):
        raise MultiHatColorwayError("Need at least four verified official Valucap 8869 colors.")
    previous: dict[str, list[str]] = {}
    for stage, value in (pinned or {}).items():
        validated = validated_palette(stage, value)
        if validated and set(validated).issubset(choices):
            previous[stage] = validated

    nonce = run_key if run_key is not None else str(uuid4())
    seed = hashlib.sha256(
        f"{tenant_id}:{source_plan_id}:{source_revision}:{nonce}:slot5-multicolor".encode()
    ).digest()
    rng = random.Random(int.from_bytes(seed, "big"))
    results = dict(previous)
    already_used = set().union(*previous.values()) if previous else set()

    for stage, count in STAGE_CAP_COUNTS.items():
        if stage in results:
            continue
        selection: list[str] = []
        group_order = ("warm", "blue", "green", "neutral") if count == 4 else ("warm", "blue", "green")
        for family in group_order:
            family_pool = [color for color in choices if COLOR_FAMILIES[color] == family]
            fresh = [color for color in family_pool if color not in already_used]
            candidates = fresh or family_pool
            if candidates:
                selection.append(rng.choice(candidates))
        while len(selection) < count:
            remaining = [color for color in choices if color not in selection]
            existing_families = {COLOR_FAMILIES[color] for color in selection}
            new_family = [color for color in remaining if COLOR_FAMILIES[color] not in existing_families]
            preferred = new_family or remaining
            unused = [color for color in preferred if color not in already_used]
            selection.append(rng.choice(unused or preferred))
        if len(selection) != count or len(set(selection)) != count:
            raise MultiHatColorwayError("Cannot select distinct approved cap colors.")
        results[stage] = selection
        already_used.update(selection)
    return results


def different_regeneration_palette(
    stage: str, current: Sequence[str], previous: Sequence[str],
    available: Sequence[str],
) -> list[str]:
    """Ensure explicit Regenerate changes the color set when stock allows.

    Reuse a visually matching *family* if possible so changing an output
    remains as colorful as the original 4/4/3 recipe.
    """
    selected = validated_palette(stage, current)
    prior = validated_palette(stage, previous)
    if not selected or not prior or set(selected) != set(prior):
        return selected
    options = sorted(set(available) & COLOR_NAMES.keys())
    rng = random.SystemRandom()
    for index, key in enumerate(selected):
        alternatives = [
            value for value in options
            if value not in selected and COLOR_FAMILIES[value] == COLOR_FAMILIES[key]
        ]
        if alternatives:
            result = list(selected)
            result[index] = rng.choice(alternatives)
            return result
    unused = [value for value in options if value not in selected]
    if unused:
        result = list(selected)
        result[rng.randrange(len(result))] = rng.choice(unused)
        return result
    return selected


def pinned_stock_hashes(settings, stage: str, palette: Sequence[str]) -> dict[str, str]:
    selected = validated_palette(stage, palette)
    if not selected:
        raise MultiHatColorwayError("Invalid approved 8869 Multi-hat palette.")
    result: dict[str, str] = {}
    for color in selected:
        try:
            data = _stock_bytes(settings, color)
        except (OSError, ValueError, ColorwayError) as exc:
            raise MultiHatColorwayError(f"Official Valucap 8869 stock is missing for {color}.") from exc
        result[color] = hashlib.sha256(data).hexdigest()
    return result


def checked_palette_stock(
    settings, stage: str, palette: object, pinned_hashes: object,
) -> list[tuple[str, bytes]]:
    """Load stock exactly as pinned at queue time; fail closed if it changed."""
    selected = validated_palette(stage, palette)
    if not selected or not isinstance(pinned_hashes, dict):
        raise MultiHatColorwayError("The Multi-hat colors were not pinned.")
    expected = set(selected)
    if set(pinned_hashes) != expected:
        raise MultiHatColorwayError("Missing per-color stock revision evidence.")
    result = []
    for color in selected:
        if not isinstance(pinned_hashes[color], str) or len(pinned_hashes[color]) != 64:
            raise MultiHatColorwayError("Invalid Multi-hat stock digest.")
        try:
            data = _stock_bytes(settings, color)
        except (OSError, ValueError, ColorwayError) as exc:
            raise MultiHatColorwayError(f"Official stock for {color} is unavailable.") from exc
        if hashlib.sha256(data).hexdigest() != pinned_hashes[color]:
            raise MultiHatColorwayError(f"Approved stock changed after queueing: {color}.")
        result.append((color, data))
    return result


def make_palette_board(
    stage: str, verified_stock: Sequence[tuple[str, bytes]],
) -> ReferenceImageInput:
    """Provide one compact board containing each *different* approved cap color.

    This is reference-only; the model must generate a single coherent photograph
    rather than reproducing a collage. Labels align with cap numbering in prompt.
    """
    colors = [key for key, _ in verified_stock]
    if validated_palette(stage, colors) != colors:
        raise MultiHatColorwayError("Invalid/duplicate stock colors on palette board.")

    board = Image.new("RGB", (1500, 1320), "#edf0f4")
    draw = ImageDraw.Draw(board)
    for index, (key, binary) in enumerate(verified_stock):
        x, y = (index % 2) * 750, (index // 2) * 660
        draw.rounded_rectangle((x + 14, y + 14, x + 735, y + 645), radius=24, fill="#ffffff")
        try:
            with Image.open(BytesIO(binary)) as original:
                if not (original.width > 0 and original.height > 0
                        and original.width * original.height <= MAX_STOCK_PIXELS):
                    raise MultiHatColorwayError("Official stock dimensions invalid.")
                oriented = ImageOps.exif_transpose(original)
                oriented.load()
                photo = ImageOps.contain(oriented.convert("RGB"), (680, 544), Image.Resampling.LANCZOS)
                board.paste(photo, (x + (750 - photo.width) // 2, y + 25 + (550 - photo.height) // 2))
                photo.close()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise MultiHatColorwayError(f"Invalid approved stock photo: {key}.") from exc
        draw.text((x + 39, y + 587), f"HAT {index + 1}: {COLOR_NAMES[key]}", fill="#1d2840")
    out = BytesIO()
    board.save(out, format="JPEG", quality=88, optimize=True)
    board.close()
    return ReferenceImageInput(
        image=_prepared_image(out.getvalue()),
        role="official_multicolor_cap_reference",
        label="Verified 8869 factory front photos: each separate numbered cap color, NOT an output collage",
    )


def multihat_color_prompt(stage: str, palette: Sequence[str]) -> str:
    selected = validated_palette(stage, palette)
    if not selected:
        raise MultiHatColorwayError("Missing distinct approved cap colorways.")
    lines = "; ".join(
        f"hat {i}: {COLOR_NAMES[key]} ({key})" for i, key in enumerate(selected, 1)
    )
    return (
        f"EXACTLY {len(selected)} different physical Valucap 8869 hats in ONE natural product "
        f"photograph. Every hat MUST be a DIFFERENT approved hat colorway. "
        f"Apply the numbered approved stock colors as follows: {lines}. "
        "The uploaded OFFICIAL MULTICOLOR REFERENCE BOARD shows exact factory crown "
        "and visor colors for each named hat. Match the cap panel fabric, brim, "
        "button and side color accurately for EACH individual hat. "
        "Never recolor all caps to the same base stock color, do not copy all "
        "cap colors from a composition reference, do not invent additional caps "
        "or unapproved color combinations. "
        "All hats MUST carry exactly the SAME user-provided embroidery artwork "
        "(same text, layout, stitching and thread colors); ONLY the hat's physical "
        "factory colorway changes. Show the artwork naturally in correct perspective. "
        "Produce one realistic photograph, not a grid, swatch sheet or collage. "
    )
