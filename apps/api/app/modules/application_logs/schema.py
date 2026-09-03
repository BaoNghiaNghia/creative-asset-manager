from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

LOG_RETENTION_DAYS = 10
MAX_LOG_PAYLOAD_BYTES = 256 * 1024


class LogApplicationCreateRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,127}$")
    display_name: str = Field(min_length=1, max_length=255)
    payload_schema: dict[str, Any] | None = None


class LogApplicationUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    payload_schema: dict[str, Any] | None = None
    active: bool | None = None


class LogApplicationResponse(BaseModel):
    id: str
    tenant_id: str
    slug: str
    display_name: str
    payload_schema: dict[str, Any] | None
    key_prefix: str
    active: bool
    retention_days: int = LOG_RETENTION_DAYS
    created_at: datetime
    updated_at: datetime


class LogApplicationCreatedResponse(LogApplicationResponse):
    api_key: str


class ApplicationLogCreateRequest(BaseModel):
    level: Literal["trace", "debug", "info", "warning", "error", "critical"] = "info"
    event_type: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    message: str | None = Field(default=None, max_length=20_000)
    trace_id: str | None = Field(default=None, min_length=1, max_length=255)
    occurred_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return value


class ApplicationLogResponse(BaseModel):
    id: str
    application_id: str
    application_slug: str
    level: str
    event_type: str
    message: str | None
    trace_id: str | None
    payload: dict[str, Any]
    occurred_at: datetime
    received_at: datetime
    expires_at: datetime


class ApplicationLogListResponse(BaseModel):
    items: list[ApplicationLogResponse]
    total: int
    offset: int
    limit: int
    retention_days: int = LOG_RETENTION_DAYS
