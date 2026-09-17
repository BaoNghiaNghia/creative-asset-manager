from datetime import datetime
from enum import Enum
from typing import Any

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator


ANNOTATION_PREVIEW_LIMIT = 500
DISPLAY_NAME_LIMIT = 160
SHARE_NAME_LIMIT = 255
FILENAME_LIMIT = 1024
MEDIA_TYPE_LIMIT = 255
RESOLVER_LIMIT = 512


class BoardIssueStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    ALL = "all"


class BoardIssueSort(str, Enum):
    NEWEST = "newest"
    OLDEST = "oldest"
    RECENTLY_UPDATED = "recently_updated"
    RECENTLY_RESOLVED = "recently_resolved"


class BoardIssueFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: BoardIssueStatus = BoardIssueStatus.OPEN
    share_id: str | None = Field(default=None, min_length=1, max_length=36)
    external_source_id: str | None = Field(default=None, min_length=1, max_length=36)
    folder_external_id: str | None = Field(default=None, min_length=1, max_length=2048)
    asset_id: str | None = Field(default=None, min_length=1, max_length=36)
    reviewer: str | None = Field(default=None, min_length=1, max_length=160)
    pinned: bool | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    sort: BoardIssueSort = BoardIssueSort.NEWEST
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)

    @model_validator(mode="after")
    def validate_range(self) -> "BoardIssueFilters":
        for value in (self.created_from, self.created_to):
            if value is not None and value.utcoffset() is None:
                raise ValueError("created date filters must include a timezone")
        if self.created_from and self.created_to and self.created_from > self.created_to:
            raise ValueError("created_from must be before or equal to created_to")
        return self


def parse_board_issue_filters(
    status: BoardIssueStatus = Query(BoardIssueStatus.OPEN),
    share_id: str | None = Query(None, min_length=1, max_length=36),
    external_source_id: str | None = Query(None, min_length=1, max_length=36),
    folder_external_id: str | None = Query(None, min_length=1, max_length=2048),
    asset_id: str | None = Query(None, min_length=1, max_length=36),
    reviewer: str | None = Query(None, min_length=1, max_length=160),
    pinned: bool | None = Query(None),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    sort: BoardIssueSort = Query(BoardIssueSort.NEWEST),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> BoardIssueFilters:
    try:
        return BoardIssueFilters(
            status=status,
            share_id=share_id,
            external_source_id=external_source_id,
            folder_external_id=folder_external_id,
            asset_id=asset_id,
            reviewer=reviewer,
            pinned=pinned,
            created_from=created_from,
            created_to=created_to,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_board_filters"}
        ) from exc


class ReviewerDTO(BaseModel):
    display_name: str


class ShareDTO(BaseModel):
    id: str
    name: str


class BoardAssetDTO(BaseModel):
    asset_id: str
    source_asset_id: str
    filename: str | None
    media_type: str | None


class ResolverDTO(BaseModel):
    actor_id: str


class BoardIssueDTO(BaseModel):
    id: str
    status: str
    annotation_preview: str
    created_at: datetime
    updated_at: datetime
    anchor_x: float | None
    anchor_y: float | None
    reviewer: ReviewerDTO
    share: ShareDTO
    asset: BoardAssetDTO
    reply_count: int
    resolved_at: datetime | None
    resolver: ResolverDTO | None


class BoardReplyDTO(BaseModel):
    id: str
    content_json: dict[str, Any]
    plain_text: str
    created_at: datetime
    updated_at: datetime
    reviewer: ReviewerDTO


class BoardIssueDetailDTO(BoardIssueDTO):
    content_json: dict[str, Any]
    plain_text: str
    replies: list[BoardReplyDTO]


class BoardIssueListDTO(BaseModel):
    items: list[BoardIssueDTO]
    page: int
    page_size: int
    total: int


class BoardStatsDTO(BaseModel):
    total_issues: int
    open_issues: int
    resolved_issues: int
    resolution_rate: int
    assets_with_open_issues: int
    shares_with_open_issues: int

