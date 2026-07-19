from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.modules.search.query_parser import (
    ClauseKind,
    ParsedSearchQuery,
    QueryMode,
    SearchClause,
)


@dataclass(frozen=True, slots=True)
class SearchQueryConfig:
    field_aliases: Mapping[str, str] = field(
        default_factory=lambda: {
            "text": "search_text",
            "filename": "filename",
            "folder": "folder_path",
            "number": "numbers",
        }
    )
    facet_names: frozenset[str] = frozenset()
    path_aliases: Mapping[str, str] = field(default_factory=dict)
    boost_paths: Mapping[str, float] = field(default_factory=dict)
    soft_and_minimum_should_match: str = "75%"


class ElasticsearchQueryBuilder:
    EXACT_NUMBER_BOOST = 16.0
    EXACT_PHRASE_BOOST = 14.0
    EXACT_TERM_BOOST = 12.0
    BOOSTED_PATH_BOOST = 10.0
    NORMALIZED_TERM_BOOST = 8.0
    FILENAME_BOOST = 6.0
    SEARCH_TEXT_BOOST = 4.0
    FOLDER_BOOST = 2.0

    def build(
        self,
        parsed: ParsedSearchQuery,
        *,
        tenant_id: str,
        config: SearchQueryConfig | None = None,
        size: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if size < 1 or size > 1_000 or offset < 0:
            raise ValueError("invalid search pagination")
        query_config = config or SearchQueryConfig()
        clauses: list[dict[str, Any]] = []
        try:
            clauses = [self._clause(item, query_config) for item in parsed.clauses]
        except ValueError:
            clauses = [self._plain_term(item.value, query_config) for item in parsed.clauses]

        if not clauses:
            content: dict[str, Any] = {"match_none": {}}
        elif parsed.mode is QueryMode.STRICT_AND:
            content = {"bool": {"must": clauses}}
        elif parsed.mode in {QueryMode.OR, QueryMode.FALLBACK}:
            content = {"bool": {"should": clauses, "minimum_should_match": 1}}
        elif parsed.mode is QueryMode.SOFT_AND:
            content = {
                "bool": {
                    "should": clauses,
                    "minimum_should_match": query_config.soft_and_minimum_should_match,
                }
            }
        else:
            content = clauses[0]

        return {
            "from": offset,
            "size": size,
            "query": {
                "bool": {
                    "filter": [{"term": {"tenant_id": tenant_id}}],
                    "must": [content],
                }
            },
        }

    def _clause(
        self, clause: SearchClause, config: SearchQueryConfig
    ) -> dict[str, Any]:
        if clause.kind is ClauseKind.TERM:
            return self._plain_term(clause.value, config)
        if clause.kind is ClauseKind.PHRASE:
            return self._phrase(clause.value, config)
        if clause.field is None:
            raise ValueError("qualified clause has no field")
        target = self._resolve_field(clause.field, config)
        return self._qualified(target, clause.value, clause.kind, config)

    def _plain_term(self, value: str, config: SearchQueryConfig) -> dict[str, Any]:
        should: list[dict[str, Any]] = []
        if value.isdigit():
            should.append({"term": {"numbers": {"value": value, "boost": self.EXACT_NUMBER_BOOST}}})
        should.extend(
            [
                {"term": {"search_terms": {"value": value, "boost": self.EXACT_TERM_BOOST}}},
                *self._boosted_paths(value, config),
                {"term": {"normalized_terms": {"value": value, "boost": self.NORMALIZED_TERM_BOOST}}},
                {"match": {"filename": {"query": value, "boost": self.FILENAME_BOOST}}},
                {"match": {"search_text": {"query": value, "boost": self.SEARCH_TEXT_BOOST}}},
                {"match": {"folder_path": {"query": value, "boost": self.FOLDER_BOOST}}},
            ]
        )
        return {"bool": {"should": should, "minimum_should_match": 1}}

    def _phrase(self, value: str, config: SearchQueryConfig) -> dict[str, Any]:
        return {
            "bool": {
                "should": [
                    {"term": {"phrases": {"value": value, "boost": self.EXACT_PHRASE_BOOST}}},
                    *self._boosted_paths(value, config),
                    {"match_phrase": {"filename": {"query": value, "boost": self.FILENAME_BOOST}}},
                    {"match_phrase": {"search_text": {"query": value, "boost": self.SEARCH_TEXT_BOOST}}},
                    {"match_phrase": {"folder_path": {"query": value, "boost": self.FOLDER_BOOST}}},
                ],
                "minimum_should_match": 1,
            }
        }

    @staticmethod
    def _resolve_field(field_name: str, config: SearchQueryConfig) -> str:
        if field_name in config.field_aliases:
            return config.field_aliases[field_name]
        if field_name in config.facet_names:
            return f"facet:{field_name}"
        if field_name in config.path_aliases:
            return f"path:{config.path_aliases[field_name]}"
        raise ValueError("unknown qualified field")

    def _qualified(
        self,
        target: str,
        value: str,
        kind: ClauseKind,
        config: SearchQueryConfig,
    ) -> dict[str, Any]:
        if target.startswith("facet:"):
            name = target.removeprefix("facet:")
            return {"term": {f"facets.{name}": {"value": value, "boost": self.EXACT_TERM_BOOST}}}
        if target.startswith("path:"):
            return self._nested_path(
                target.removeprefix("path:"), value, self.BOOSTED_PATH_BOOST
            )
        allowed = {"search_text", "filename", "folder_path", "numbers", "search_terms", "normalized_terms", "phrases"}
        if target not in allowed:
            raise ValueError("unsafe qualified field")
        if kind is ClauseKind.QUALIFIED_PHRASE and target in {"search_text", "filename", "folder_path"}:
            return {"match_phrase": {target: {"query": value, "boost": self.EXACT_PHRASE_BOOST}}}
        return {"term": {target: {"value": value, "boost": self.EXACT_TERM_BOOST}}}

    def _boosted_paths(
        self, value: str, config: SearchQueryConfig
    ) -> list[dict[str, Any]]:
        return [
            self._nested_path(path, value, self.BOOSTED_PATH_BOOST * min(boost, 1.0))
            for path, boost in sorted(config.boost_paths.items())
            if boost > 0
        ]

    @staticmethod
    def _nested_path(path: str, value: str, boost: float) -> dict[str, Any]:
        return {
            "nested": {
                "path": "path_values",
                "score_mode": "max",
                "query": {
                    "bool": {
                        "filter": [{"term": {"path_values.path": path}}],
                        "must": [{"term": {"path_values.value": value}}],
                    }
                },
                "boost": boost,
            }
        }
