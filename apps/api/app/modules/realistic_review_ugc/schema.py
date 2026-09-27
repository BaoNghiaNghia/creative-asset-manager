from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CampaignStatus = Literal["running", "paused", "completed", "stopped"]
ScoutStatus = Literal["offline", "ready", "busy", "needs_login", "error"]
CandidateStatus = Literal[
    "discovered",
    "importing",
    "drive_ready",
    "import_failed",
    "rejected_duplicate",
]


class CampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=500)
    target_count: int = Field(default=100, ge=1, le=5000)
    max_scroll_batches: int = Field(default=5, ge=1, le=50)
    auto_import: bool = False


class CampaignResponse(BaseModel):
    id: str
    name: str
    query: str
    target_count: int
    max_scroll_batches: int
    auto_import: bool
    status: CampaignStatus
    scout_status: ScoutStatus
    scout_last_seen_at: datetime | None
    discovered: int = 0
    drive_ready: int = 0
    failed: int = 0
    created_at: datetime
    updated_at: datetime


class CampaignCreatedResponse(CampaignResponse):
    scout_token: str


class CandidateSubmission(BaseModel):
    pin_url: str = Field(min_length=1, max_length=2048)
    image_url: str = Field(min_length=1, max_length=4096)
    alt_text: str | None = Field(default=None, max_length=2000)


class CandidateBatchRequest(BaseModel):
    items: list[CandidateSubmission] = Field(min_length=1, max_length=50)


class CandidateResponse(BaseModel):
    id: str
    campaign_id: str
    pin_url: str
    image_url: str
    alt_text: str | None
    status: CandidateStatus
    content_hash: str | None
    width: int | None
    height: int | None
    size_bytes: int | None
    remote_file_id: str | None
    remote_folder_id: str | None
    web_url: str | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class CandidateBatchResponse(BaseModel):
    created: int
    existing: int
    items: list[CandidateResponse]


class ScoutHeartbeatRequest(BaseModel):
    status: ScoutStatus


class ScoutTaskResponse(BaseModel):
    campaign_id: str
    query: str
    target_count: int
    max_scroll_batches: int
    auto_import: bool
    status: CampaignStatus
    discovered: int
    drive_ready: int


class ImportResponse(BaseModel):
    candidate: CandidateResponse
