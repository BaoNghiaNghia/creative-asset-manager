from dataclasses import dataclass
from typing import Mapping

@dataclass(frozen=True, slots=True)
class PlatformProfile:
    platform: str
    required_aspect_ratios: tuple[str, ...]
    output_ratio_folders: tuple[str, ...]

_PROFILES = {
    "etsy": PlatformProfile("etsy", ("1:1",), ("1x1",)),
    "amazon": PlatformProfile("amazon", ("16:9","9:16"), ("16x9","9x16")),
}
def platform_profile(platform: str) -> PlatformProfile:
    try: return _PROFILES[str(platform)]
    except KeyError as exc: raise ValueError("unsupported creative platform") from exc
