from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SquareGeometry:
    source_width: int
    source_height: int
    target_size: int
    normalized_width: int
    normalized_height: int


def calculate_square_geometry(source_width: int, source_height: int, target_size: int) -> SquareGeometry:
    """Scale to fit the requested square without crop or distortion."""
    if source_width <= 0 or source_height <= 0:
        raise ValueError("image_generation_invalid_dimensions")
    if target_size not in (1024, 2048):
        raise ValueError("image_generation_target_size_unsupported")
    scale = target_size / max(source_width, source_height)
    normalized_width = max(1, round(source_width * scale))
    normalized_height = max(1, round(source_height * scale))
    return SquareGeometry(source_width, source_height, target_size, normalized_width, normalized_height)
