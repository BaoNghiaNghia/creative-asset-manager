from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SquareGeometry:
    source_width: int
    source_height: int
    target_size: int
    normalized_width: int
    normalized_height: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def already_square(self) -> bool:
        return self.source_width == self.source_height


def calculate_square_geometry(
    source_width: int,
    source_height: int,
    target_size: int,
) -> SquareGeometry:
    """Scale to fit and center inside a square without crop or distortion."""
    if source_width <= 0 or source_height <= 0:
        raise ValueError("image_generation_invalid_dimensions")
    if target_size not in (1024, 2048):
        raise ValueError("image_generation_target_size_unsupported")
    scale = target_size / max(source_width, source_height)
    normalized_width = max(1, min(target_size, round(source_width * scale)))
    normalized_height = max(1, min(target_size, round(source_height * scale)))
    horizontal = target_size - normalized_width
    vertical = target_size - normalized_height
    left = horizontal // 2
    top = vertical // 2
    return SquareGeometry(
        source_width=source_width,
        source_height=source_height,
        target_size=target_size,
        normalized_width=normalized_width,
        normalized_height=normalized_height,
        left=left,
        top=top,
        right=horizontal - left,
        bottom=vertical - top,
    )
