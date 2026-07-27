from typing import Any, Literal
from pydantic import BaseModel, Field

class SearchV2Request(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    source_provider: Literal["google-drive", "sharepoint"] | None = None
    facets: dict[str, list[str]] = Field(default_factory=dict)
    offset: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=200)
    debug: bool = False

class SearchV2Response(BaseModel):
    search_version: Literal["v2", "v3"]
    items: list[dict[str, Any]]
    total: int
    facets: dict[str, list[dict[str, Any]]]
    parsed_query: dict[str, Any] | None = None
    took_ms: int | None = None

class SearchCapabilities(BaseModel):
    selected_version: Literal["v1", "v2", "v3"]
    v2_available: bool
    parser_available: bool
    debug_allowed: bool
    facet_names: list[str]
    examples: list[str]


class SearchSuggestion(BaseModel):
    text: str
    prefix: str
    completion: str
    kind: Literal["filename", "visible_text"]

class SearchSuggestionsResponse(BaseModel):
    search_version: Literal["v2", "v3"]
    suggestions: list[SearchSuggestion]
    took_ms: int | None = None
