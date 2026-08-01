from __future__ import annotations

import base64
import binascii
import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.modules.search.query_parser import (
    ClauseKind,
    ParsedSearchQuery,
    QueryMode,
    SearchClause,
)


_CURSOR_VERSION = 1
_CURSOR_MAX_LENGTH = 512


def encode_search_cursor(sort_values: list[Any]) -> str:
    """Encode the stable Elasticsearch search_after values for one result page."""
    if len(sort_values) != 2:
        raise ValueError("invalid search cursor sort values")
    score, asset_id = sort_values
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError("invalid search cursor score")
    if not isinstance(asset_id, str) or not asset_id or len(asset_id) > 256:
        raise ValueError("invalid search cursor asset id")
    payload = json.dumps(
        {"v": _CURSOR_VERSION, "s": [score, asset_id]},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_search_cursor(cursor: str) -> list[Any]:
    """Validate and decode an opaque search_after cursor supplied by a client."""
    if not isinstance(cursor, str) or not cursor or len(cursor) > _CURSOR_MAX_LENGTH:
        raise ValueError("invalid search cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        document = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(document.decode("ascii"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid search cursor") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise ValueError("invalid search cursor")
    sort_values = payload.get("s")
    if not isinstance(sort_values, list):
        raise ValueError("invalid search cursor")
    encode_search_cursor(sort_values)
    return sort_values


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
    VISIBLE_TEXT_BOOST = 20.0
    PREFIX_BOOST = 7.0
    FUZZY_BOOST = 3.0

    def build(
        self,
        parsed: ParsedSearchQuery,
        *,
        tenant_id: str,
        config: SearchQueryConfig | None = None,
        size: int = 50,
        offset: int = 0,
        search_after: list[Any] | None = None,
    ) -> dict[str, Any]:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if size < 1 or size > 1_000 or offset < 0 or (search_after is not None and offset):
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

        document = {
            "size": size,
            "sort": [{"_score": "desc"}, {"asset_id": "asc"}],
            "_source": [
                "asset_id",
                "source_id",
                "filename",
                "folder_path",
            ],
            "query": {
                "bool": {
                    "filter": [{"term": {"tenant_id": tenant_id}}],
                    "must": [content],
                }
            },
        }
        if search_after is None:
            document["from"] = offset
        else:
            document["search_after"] = search_after
        return document

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
                {"match_phrase": {"visible_text": {"query": value, "boost": self.VISIBLE_TEXT_BOOST}}},
                {"term": {"search_terms": {"value": value, "boost": self.EXACT_TERM_BOOST}}},
                *self._boosted_paths(value, config),
                {"term": {"normalized_terms": {"value": value, "boost": self.NORMALIZED_TERM_BOOST}}},
                {"match_phrase_prefix": {"visible_text": {"query": value, "boost": self.PREFIX_BOOST}}},
                {"match_phrase_prefix": {"search_text": {"query": value, "boost": self.PREFIX_BOOST}}},
                {"multi_match": {"query": value, "type": "bool_prefix", "fields": ["search_suggest", "search_suggest._2gram", "search_suggest._3gram"], "boost": self.PREFIX_BOOST}},
                {"multi_match": {"query": value, "fields": ["visible_text^8", "filename^4", "search_text^2"], "fuzziness": "AUTO", "prefix_length": 1, "max_expansions": 30, "boost": self.FUZZY_BOOST}},
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
                    {"match_phrase": {"visible_text": {"query": value, "boost": self.VISIBLE_TEXT_BOOST}}},
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
