from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

DesignType = Literal[
    "petfull", "petoutline", "peoplefull", "peopleoutline", "carfull",
    "caroutline", "existeddesign", "roman", "monogram", "handwriting",
    "floral", "neckline", "text", "other tags",
]
SearchSortMode = Literal["relevance", "newest", "oldest", "name_asc", "name_desc"]
MediaKind = Literal["image", "video", "pdf", "document"]


class SearchCoreFilters(BaseModel):
    media_kind: list[MediaKind] = Field(default_factory=list)
    mime_type: list[str] = Field(default_factory=list)
    extension: list[str] = Field(default_factory=list)
    source_created_from: datetime | None = None
    source_created_to: datetime | None = None
    source_modified_from: datetime | None = None
    source_modified_to: datetime | None = None
    file_size_min: int | None = Field(default=None, ge=0)
    file_size_max: int | None = Field(default=None, ge=0)
    has_visible_text: bool | None = None
    has_ai_metadata: bool | None = None

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "SearchCoreFilters":
        self.media_kind = list(dict.fromkeys(self.media_kind))
        self.mime_type = list(dict.fromkeys(
            value.strip().casefold()
            for value in self.mime_type
            if value.strip()
        ))
        self.extension = list(dict.fromkeys(
            value.strip().casefold().lstrip(".")
            for value in self.extension
            if value.strip().lstrip(".")
        ))
        if self.source_created_from and self.source_created_to and self.source_created_from > self.source_created_to:
            raise ValueError("source_created_from must not exceed source_created_to")
        if self.source_modified_from and self.source_modified_to and self.source_modified_from > self.source_modified_to:
            raise ValueError("source_modified_from must not exceed source_modified_to")
        if self.file_size_min is not None and self.file_size_max is not None and self.file_size_min > self.file_size_max:
            raise ValueError("file_size_min must not exceed file_size_max")
        return self

    def has_effective_filter(self) -> bool:
        return any((
            self.media_kind,
            self.mime_type,
            self.extension,
            self.source_created_from,
            self.source_created_to,
            self.source_modified_from,
            self.source_modified_to,
            self.file_size_min is not None,
            self.file_size_max is not None,
            self.has_visible_text is not None,
            self.has_ai_metadata is not None,
        ))


class SearchV3Request(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    source_provider: Literal["google-drive", "sharepoint"] | None = None
    external_source_id: str | None = Field(default=None, max_length=128)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    filters: SearchCoreFilters = Field(default_factory=SearchCoreFilters)
    offset: int = Field(0, ge=0, le=500)
    design_types: list[DesignType] = Field(default_factory=list, max_length=3)
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(50, ge=1, le=200)
    include_facets: bool = True
    sort: SearchSortMode = "relevance"
    debug: bool = False

    @model_validator(mode="after")
    def normalize_and_require_condition(self) -> "SearchV3Request":
        self.query = (self.query or "").strip() or None
        self.facets = {
            str(name): list(dict.fromkeys(
                value.strip() for value in values
                if isinstance(value, str) and value.strip()
            ))
            for name, values in self.facets.items()
            if isinstance(values, list)
        }
        self.facets = {name: values for name, values in self.facets.items() if values}
        if not any((
            self.query,
            self.facets,
            self.design_types,
            self.filters.has_effective_filter(),
            self.source_provider,
            (self.external_source_id or "").strip(),
        )):
            raise ValueError("Search requires a query or at least one filter.")
        return self


class SearchV3Response(BaseModel):
    search_version: Literal["v3"]
    items: list[dict[str, Any]]
    total: int
    total_relation: Literal["eq", "gte"] = "eq"
    facets: dict[str, list[dict[str, Any]]]
    parsed_query: dict[str, Any] | None = None
    took_ms: int | None = None
    next_cursor: str | None = None
    has_more: bool = False


class SearchCapabilities(BaseModel):
    selected_version: Literal["v3"]
    readiness: Literal["ready", "verification_unknown", "incompatible", "unavailable"]
    search_available: bool
    viewer_scoped: bool
    failure_code: str | None = None
    facet_names: list[str]
    examples: list[str]


class SearchSuggestion(BaseModel):
    text: str
    prefix: str
    completion: str
    kind: Literal["filename", "visible_text", "search_text"]


class SearchSuggestionsResponse(BaseModel):
    search_version: Literal["v3"]
    suggestions: list[SearchSuggestion]
    took_ms: int | None = None
