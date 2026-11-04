"""Verified Madeira PolyNeon visor -> embroidery thread lookup for GatorHats.

Color codes and RGB are transcribed from the existing bundled supplier
'mauchi.txt' thread catalogue. HEX is a DIGITAL preview of the yarn, not
a measured spectrophotometer value or a claim that cap fabric equals yarn.
The supplied table defines ten named visor colors (nine unique thread SKUs).
Two camouflage-print visors intentionally have no verified matching SKU.
"""
from __future__ import annotations

from dataclasses import dataclass

PALETTE_VERSION = "madeira-polyneon-visor-v1"


@dataclass(frozen=True)
class MadeiraThread:
    visor: str
    code: int
    name: str
    hex: str

    @property
    def rgb(self) -> tuple[int, int, int]:
        return tuple(bytes.fromhex(self.hex.lstrip("#")))  # type: ignore[return-value]


# Keys match official Products Slot 7 Natural-series stock identifiers.
MADEIRA_VISOR_THREADS: dict[str, MadeiraThread] = {
    "natural-royal": MadeiraThread("Royal", 1829, "Blue", "#2464A7"),
    "natural-forest-green": MadeiraThread("Forest Green", 1750, "Emerald Green", "#006C45"),
    "natural-brown": MadeiraThread("Brown", 1859, "Dark Brown", "#36302E"),
    "natural-maroon": MadeiraThread("Maroon", 1784, "Maroon", "#5E1E28"),
    "natural-charcoal": MadeiraThread("Charcoal", 1841, "Dark Gray", "#3E4B50"),
    "natural-khaki": MadeiraThread("Khaki", 1729, "Light Brown", "#907663"),
    "natural-camo-green": MadeiraThread("Camo Green", 1750, "Emerald Green", "#006C45"),
    "natural-black": MadeiraThread("Black", 1800, "Black", "#202424"),
    "natural-navy": MadeiraThread("Navy", 1967, "Navy", "#182C48"),
    "natural-red": MadeiraThread("Red", 1637, "Red", "#AC211C"),
}

# These prints have no exact mapping in the user-supplied Madeira table.
UNMAPPED_VISORS = frozenset({
    "natural-mossy-oak-breakup", "natural-realtree-all-purpose",
})


def stage1_madeira_guidance() -> str:
    """Bounded prompt hint, never an instruction to tint the original master."""
    # Source has ten visor aliases for nine distinct Madeira thread shades.
    ordered = list({t.code: t for t in MADEIRA_VISOR_THREADS.values()}.values())
    entries = "; ".join(f"{t.code} {t.name} ({t.hex})" for t in ordered)
    return (
        "MADEIRA POLYNEON VERIFIED EMBROIDERY THREAD PALETTE "
        f"[{PALETTE_VERSION}]: {entries}. "
        "These are named real thread references with screen HEX approximations. "
        "For new Stage 1 cap typography, favor one or two of these physical "
        "thread hues when they suit the phrase and design style; other verified "
        "Madeira hues remain permitted for semantic needs. A visor has NOT been "
        "chosen at Stage 1, so do NOT auto-match, recolor or flatten the original "
        "embroidery master to a visor. Preserve the design's intended stitches, "
        "shading and colors for Stage 2. Never claim production color accuracy "
        "from RGB alone."
    )
