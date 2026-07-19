from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.modules.ai_metadata.normalizer import (
    MetadataNormalizer,
    NormalizedMetadataValue,
)
from app.modules.ai_metadata.traverser import (
    MetadataTraverser,
    normalize_logical_path,
)


@dataclass(frozen=True, slots=True)
class SearchPathValue:
    path: str
    value: str


@dataclass(frozen=True, slots=True)
class SearchProjection:
    search_text: str
    search_terms: tuple[str, ...]
    normalized_terms: tuple[str, ...]
    phrases: tuple[str, ...]
    numbers: tuple[str, ...]
    facets: Mapping[str, tuple[str, ...]]
    path_values: tuple[SearchPathValue, ...]

    def to_document(self) -> dict[str, Any]:
        return {
            "search_text": self.search_text,
            "search_terms": list(self.search_terms),
            "normalized_terms": list(self.normalized_terms),
            "phrases": list(self.phrases),
            "numbers": list(self.numbers),
            "facets": {
                name: list(values)
                for name, values in sorted(self.facets.items())
            },
            "path_values": [
                {"path": item.path, "value": item.value}
                for item in self.path_values
            ],
        }


@dataclass(frozen=True, slots=True)
class SearchProjectionBuildResult:
    projection: SearchProjection
    projection_version: str
    query_config: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    max_search_text_chars: int = 100_000
    max_search_terms: int = 10_000
    max_normalized_terms: int = 20_000
    max_phrases: int = 5_000
    max_numbers: int = 5_000
    max_facets: int = 100
    max_facet_values: int = 1_000
    max_path_values: int = 10_000
    max_term_chars: int = 512
    max_path_value_chars: int = 2_048
    max_boost_paths: int = 100

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")


class SearchProjectionBuilder:
    def __init__(
        self,
        *,
        projection_version: str = "search-projection-v1",
        traverser: MetadataTraverser | None = None,
        normalizer: MetadataNormalizer | None = None,
        limits: ProjectionLimits | None = None,
    ):
        if not projection_version.strip():
            raise ValueError("projection_version is required")
        self.projection_version = projection_version
        self.traverser = traverser or MetadataTraverser()
        self.normalizer = normalizer or MetadataNormalizer()
        self.limits = limits or ProjectionLimits()

    def build(
        self,
        metadata_json: Mapping[str, Any],
        search_config_json: Mapping[str, Any] | None = None,
    ) -> SearchProjectionBuildResult:
        config = {} if search_config_json is None else search_config_json
        if not isinstance(config, Mapping):
            raise TypeError("search_config_json must be an object")

        exclude_paths = self._string_sequence(config.get("exclude_paths"))
        include_booleans = config.get("include_booleans", False) is True
        extracted = self.traverser.traverse(
            metadata_json,
            exclude_paths=exclude_paths,
            include_booleans=include_booleans,
        )
        normalized = self.normalizer.normalize_all(extracted)

        include_all = config.get("include_all_scalar_values", True) is not False
        text_paths = self._normalized_paths(config.get("text_paths"))
        facet_paths = self._facet_paths(config)
        boost_paths = self._boost_paths(config.get("boost_paths"))
        selection_paths = tuple(
            sorted(
                {
                    *text_paths,
                    *(path for paths in facet_paths.values() for path in paths),
                    *boost_paths.keys(),
                }
            )
        )
        selected = tuple(
            item
            for item in normalized
            if include_all or self._matches_any(item.path, selection_paths)
        )

        search_terms = self._limited_unique(
            (
                item.normalized_value
                for item in selected
                if len(item.normalized_value) <= self.limits.max_term_chars
            ),
            self.limits.max_search_terms,
        )
        normalized_terms = self._limited_unique(
            (
                token
                for item in selected
                for token in item.tokens
                if len(token) <= self.limits.max_term_chars
            ),
            self.limits.max_normalized_terms,
        )
        phrases = self._limited_unique(
            (
                phrase
                for item in selected
                for phrase in item.phrases
                if len(phrase) <= self.limits.max_term_chars
            ),
            self.limits.max_phrases,
        )
        numbers = self._limited_unique(
            (number for item in selected for number in item.numbers),
            self.limits.max_numbers,
        )
        path_values = self._path_values(selected)
        facets = self._build_facets(normalized, facet_paths)

        projection = SearchProjection(
            search_text=self._search_text(search_terms),
            search_terms=search_terms,
            normalized_terms=normalized_terms,
            phrases=phrases,
            numbers=numbers,
            facets=facets,
            path_values=path_values,
        )
        return SearchProjectionBuildResult(
            projection=projection,
            projection_version=self.projection_version,
            query_config={"boost_paths": boost_paths},
        )

    def _path_values(
        self, values: Sequence[NormalizedMetadataValue]
    ) -> tuple[SearchPathValue, ...]:
        unique = {
            (item.path, item.normalized_value)
            for item in values
            if len(item.normalized_value) <= self.limits.max_path_value_chars
        }
        return tuple(
            SearchPathValue(path, value)
            for path, value in sorted(unique)[: self.limits.max_path_values]
        )

    def _build_facets(
        self,
        values: Sequence[NormalizedMetadataValue],
        configured: Mapping[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        facets: dict[str, tuple[str, ...]] = {}
        for name in sorted(configured)[: self.limits.max_facets]:
            facet_values = self._limited_unique(
                (
                    item.normalized_value
                    for item in values
                    if self._matches_any(item.path, configured[name])
                    and len(item.normalized_value) <= self.limits.max_term_chars
                ),
                self.limits.max_facet_values,
            )
            if facet_values:
                facets[name] = facet_values
        return facets

    def _facet_paths(
        self, config: Mapping[str, Any]
    ) -> dict[str, tuple[str, ...]]:
        raw = config.get("facet_paths", config.get("facets", ()))
        if isinstance(raw, Mapping):
            result: dict[str, tuple[str, ...]] = {}
            for name, paths in raw.items():
                if not isinstance(name, str):
                    continue
                normalized = self._normalized_paths(paths)
                if normalized:
                    result[name] = normalized
            return result
        paths = self._normalized_paths(raw)
        return {path: (path,) for path in paths}

    def _boost_paths(self, raw: Any) -> dict[str, float]:
        if not isinstance(raw, Mapping):
            return {}
        values: dict[str, float] = {}
        for path, boost in raw.items():
            if not isinstance(path, str) or isinstance(boost, bool):
                continue
            try:
                numeric = float(boost)
            except (TypeError, ValueError):
                continue
            normalized = normalize_logical_path(path)
            if normalized and math.isfinite(numeric) and numeric > 0:
                values[normalized] = numeric
        return dict(sorted(values.items())[: self.limits.max_boost_paths])

    def _search_text(self, terms: Sequence[str]) -> str:
        accepted: list[str] = []
        current_length = 0
        for term in terms:
            added = len(term) + (1 if accepted else 0)
            if current_length + added > self.limits.max_search_text_chars:
                break
            accepted.append(term)
            current_length += added
        return " ".join(accepted)

    @staticmethod
    def _limited_unique(values: Any, limit: int) -> tuple[str, ...]:
        return tuple(sorted({value for value in values if value}))[:limit]

    @classmethod
    def _normalized_paths(cls, raw: Any) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    normalize_logical_path(path)
                    for path in cls._string_sequence(raw)
                    if normalize_logical_path(path)
                }
            )
        )

    @staticmethod
    def _string_sequence(raw: Any) -> tuple[str, ...]:
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            return tuple(item for item in raw if isinstance(item, str))
        return ()

    @classmethod
    def _matches_any(cls, path: str, configured: Sequence[str]) -> bool:
        comparable = cls._comparable_path(path)
        return any(
            comparable == cls._comparable_path(candidate)
            or comparable.startswith(f"{cls._comparable_path(candidate)}.")
            for candidate in configured
            if candidate
        )

    @staticmethod
    def _comparable_path(path: str) -> str:
        return unicodedata.normalize("NFKC", normalize_logical_path(path)).casefold()
