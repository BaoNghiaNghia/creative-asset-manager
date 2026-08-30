from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

DesignType = Literal[
    "petfull", "petoutline", "peoplefull", "peopleoutline", "carfull",
    "caroutline", "existeddesign", "roman", "handwriting", "floral",
    "neckline", "text", "other tags",
]


class SearchV3Request(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    source_provider: Literal["google-drive", "sharepoint"] | None = None
    external_source_id: str | None = Field(default=None, max_length=128)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    offset: int = Field(0, ge=0, le=500)
    design_types: list[DesignType] = Field(default_factory=list, max_length=3)
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(50, ge=1, le=200)
    include_facets: bool = True
    sort: Literal["relevance"] = "relevance"
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
