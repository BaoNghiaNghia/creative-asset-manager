from typing import Any, Literal
from pydantic import BaseModel, Field

DesignType = Literal["petfull", "petoutline", "peoplefull", "peopleoutline", "carfull", "caroutline", "existeddesign", "roman", "handwriting", "floral", "neckline", "text", "other tags"]


class SearchV3Request(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    source_provider: Literal["google-drive", "sharepoint"] | None = None
    external_source_id: str | None = Field(default=None, max_length=128)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    offset: int = Field(0, ge=0)
    design_types: list[DesignType] = Field(default_factory=list, max_length=3)
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(50, ge=1, le=200)
    include_facets: bool = True
    debug: bool = False

class SearchV3Response(BaseModel):
    search_version: Literal["v3"]
    items: list[dict[str, Any]]
    total: int
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
