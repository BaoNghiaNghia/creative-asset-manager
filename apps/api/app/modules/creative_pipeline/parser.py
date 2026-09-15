from __future__ import annotations
from dataclasses import dataclass
from app.modules.creative_pipeline.constants import CreativePlatform

@dataclass(frozen=True, slots=True)
class ParsedSourceGroup:
    platform: CreativePlatform
    display_name: str
    original_name: str

@dataclass(frozen=True, slots=True)
class ParsedListingFolder:
    listing_key: str
    original_name: str

def parse_source_group_name(name: str | None) -> ParsedSourceGroup | None:
    if not isinstance(name, str):
        return None
    original = name
    normalized = name.strip()
    for prefix, platform in (("etsy", CreativePlatform.ETSY), ("amazon", CreativePlatform.AMAZON)):
        marker = f"{prefix} - "
        if normalized.lower().startswith(marker):
            suffix = normalized[len(marker):].strip()
            if not suffix:
                return None
            return ParsedSourceGroup(platform=platform, display_name=suffix, original_name=original)
    return None

def parse_listing_folder_name(name: str | None) -> ParsedListingFolder | None:
    if not isinstance(name, str):
        return None
    original = name
    normalized = name.strip()
    marker = "listing - "
    if not normalized.lower().startswith(marker):
        return None
    key = normalized[len(marker):].strip()
    if not key:
        return None
    return ParsedListingFolder(listing_key=key, original_name=original)
