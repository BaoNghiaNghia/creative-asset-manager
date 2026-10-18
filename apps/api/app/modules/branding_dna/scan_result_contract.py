"""Strict metadata-only validation for future Branding DNA result receipts.

A digest is an integrity identifier, NOT marketplace provenance or media rights.
This module makes no network requests and never promotes any imported evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_RESULT_IMAGES = 20
MAX_RESULT_PAGES = 5
KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$"
REV_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
HASH_PATTERN = r"^[0-9a-f]{64}$"


class ObservedGalleryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sequence_index: int = Field(ge=0, le=MAX_RESULT_IMAGES - 1)
    asset_sha256: str = Field(pattern=HASH_PATTERN)
    source_url_sha256: str = Field(pattern=HASH_PATTERN)
    role: Literal["hero", "detail", "lifestyle", "ugc",
                  "information", "variation", "unknown"]
    rights_state: Literal["unknown"] = "unknown"


class ResultManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_key: str = Field(strict=True, pattern=KEY_PATTERN)
    observed_revision: str = Field(strict=True, pattern=REV_PATTERN)
    captured_at: datetime
    page_count: int = Field(strict=True, ge=1, le=MAX_RESULT_PAGES)
    images: list[ObservedGalleryItem] = Field(min_length=1, max_length=MAX_RESULT_IMAGES)

    @field_validator("captured_at", mode="before")
    @classmethod
    def timestamp_is_rfc3339_or_datetime(cls, value):
        # FastAPI validates a Python dict after decoding JSON; a global
        # strict model rejects every ISO timestamp string, even valid ones.
        if not isinstance(value, (str, datetime)):
            raise ValueError("Captured timestamp must be RFC3339 text")
        return value

    @model_validator(mode="after")
    def validate_order_and_time(self):
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("Captured timestamp requires an explicit timezone")
        self.captured_at = self.captured_at.astimezone(timezone.utc)
        seq = [image.sequence_index for image in self.images]
        if seq != list(range(len(seq))):
            raise ValueError("Gallery must be contiguous in source order, starting at zero")
        return self


def manifest_digest(*, tenant_id: str, shop_id: str, task_id: str,
                    snapshot_id: str, snapshot_sha256: str, manifest: ResultManifest) -> str:
    """Bind exact metadata bytes to CAM's tenant/shop/task/snapshot identity.

    This hash only detects differing submitted values; it is not signed by a
    platform and does not prove facts, scan permissions, images or sales.
    """
    material = {
        "version": 1, "tenant_id": tenant_id, "shop_id": shop_id,
        "task_id": task_id, "snapshot_id": snapshot_id,
        "snapshot_sha256": snapshot_sha256,
        "manifest": manifest.model_dump(mode="json"),
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def valid_capture_window(captured_at: datetime, *, now: datetime) -> bool:
    captured = captured_at.astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    return (current - timedelta(days=30) <= captured <=
            current + timedelta(minutes=5))
