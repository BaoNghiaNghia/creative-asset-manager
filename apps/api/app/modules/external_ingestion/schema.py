from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_INGESTION_ITEMS = 1_000
MAX_INGESTION_PAYLOAD_BYTES = 1_048_576
_EXTERNAL_ID_RE = re.compile(r"^[^\s\x00-\x1f\x7f]+$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")


class AssetIngestionItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_asset_id: str = Field(min_length=1, max_length=2_048)
    download_url: str = Field(min_length=1, max_length=4_096)
    checksum: str | None = Field(default=None, min_length=1, max_length=255)
    filename: str | None = Field(default=None, min_length=1, max_length=1_024)
    modified_at: datetime | None = None

    @field_validator("external_asset_id")
    @classmethod
    def validate_external_asset_id(cls, value: str) -> str:
        if value in {".", ".."} or not _EXTERNAL_ID_RE.fullmatch(value):
            raise ValueError("external_asset_id contains invalid characters")
        return value

    @field_validator("download_url")
    @classmethod
    def validate_download_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("download_url must be an absolute HTTPS URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("download_url must not contain credentials or a fragment")
        return value

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        if value is not None and ("/" in value or "\\" in value or any(ord(ch) < 32 for ch in value)):
            raise ValueError("filename must be a plain file name")
        return value

    @field_validator("modified_at")
    @classmethod
    def validate_modified_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("modified_at must include a timezone")
        return value


class AssetIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=36)
    items: list[AssetIngestionItemRequest] = Field(
        min_length=1,
        max_length=MAX_INGESTION_ITEMS,
    )

    @model_validator(mode="after")
    def validate_unique_external_ids(self) -> "AssetIngestionRequest":
        external_ids = [item.external_asset_id for item in self.items]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("external_asset_id must be unique within an ingestion")
        return self


def validate_idempotency_key(value: str) -> str:
    if not _IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise ValueError("Idempotency-Key has an invalid format")
    return value


class AssetIngestionAcceptedResponse(BaseModel):
    ingestion_id: str
    status: str
    received: int


class AssetIngestionStatusResponse(BaseModel):
    ingestion_id: str
    source_id: str
    status: str
    received: int
    queued: int
    processing: int
    completed: int
    failed: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class AssetIngestionItemResponse(BaseModel):
    item_id: str
    external_asset_id: str
    filename: str | None
    status: str
    processing_job_id: str | None
    source_asset_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class AssetIngestionItemsResponse(BaseModel):
    ingestion_id: str
    total: int
    offset: int
    limit: int
    items: list[AssetIngestionItemResponse]
