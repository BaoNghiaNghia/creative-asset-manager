from __future__ import annotations

import base64
import binascii
import hashlib
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


_CURSOR_VERSION = 2
_CURSOR_MAX_LENGTH = 4096
_MAX_OFFSET = 500


@dataclass(frozen=True, slots=True)
class SearchCursorV2:
    sort_values: list[Any]
    fingerprint: str
    pit_id: str


def search_request_fingerprint(payload: Mapping[str, Any]) -> str:
    """Hash canonical effective search semantics without exposing raw values."""
    document = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(document).hexdigest()


def _validate_sort_values(sort_values: Any) -> list[Any]:
    if not isinstance(sort_values, list) or len(sort_values) != 2:
        raise ValueError("invalid search cursor sort values")
    primary, asset_id = sort_values
    if isinstance(primary, bool) or not isinstance(
        primary, (int, float, str, type(None))
    ):
        raise ValueError("invalid search cursor primary sort value")
    if isinstance(primary, float) and not math.isfinite(primary):
        raise ValueError("invalid search cursor primary sort value")
    if isinstance(primary, str) and len(primary) > 1024:
        raise ValueError("invalid search cursor primary sort value")
    if not isinstance(asset_id, str) or not asset_id or len(asset_id) > 256:
        raise ValueError("invalid search cursor asset id")
    return sort_values


def encode_search_cursor(
    sort_values: list[Any],
    *,
    fingerprint: str,
    pit_id: str,
) -> str:
    """Encode query-bound PIT/search_after state without embedding raw filters."""
    values = _validate_sort_values(sort_values)
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
    ):
        raise ValueError("invalid search cursor fingerprint")
    if not isinstance(pit_id, str) or not pit_id or len(pit_id) > 2048:
        raise ValueError("invalid search cursor PIT")
    payload = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "sort": values,
            "fingerprint": fingerprint,
            "pit": pit_id,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    cursor = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    if len(cursor) > _CURSOR_MAX_LENGTH:
        raise ValueError("search cursor exceeds maximum length")
    return cursor


def decode_search_cursor(
    cursor: str,
    *,
    expected_fingerprint: str,
) -> SearchCursorV2:
    """Validate an opaque V2 cursor and bind it to current request semantics."""
    if not isinstance(cursor, str) or not cursor or len(cursor) > _CURSOR_MAX_LENGTH:
        raise ValueError("invalid search cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        document = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        )
        payload = json.loads(document.decode("ascii"))
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise ValueError("invalid search cursor") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise ValueError("unsupported search cursor version")
    fingerprint = payload.get("fingerprint")
    if fingerprint != expected_fingerprint:
        raise ValueError("search cursor does not match the current request")
    pit_id = payload.get("pit")
    values = _validate_sort_values(payload.get("sort"))
    # Re-encode to apply all scalar/length checks symmetrically.
    encode_search_cursor(values, fingerprint=fingerprint, pit_id=pit_id)
    return SearchCursorV2(values, fingerprint, pit_id)


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
    EXACT_FILENAME_BOOST = 18.0
    EXACT_NUMBER_BOOST = 16.0
    EXACT_PHRASE_BOOST = 14.0
    EXACT_TERM_BOOST = 12.0
    BOOSTED_PATH_BOOST = 10.0
    NORMALIZED_TERM_BOOST = 9.0
    VISIBLE_TEXT_BOOST = 8.0
    FILENAME_BOOST = 7.0
    SEARCH_TEXT_BOOST = 5.0
    PREFIX_BOOST = 3.0
    FOLDER_BOOST = 2.0
    FUZZY_BOOST = 0.8
    FUZZY_MIN_LENGTH = 4
    FUZZY_MAX_EXPANSIONS = 24

    def build(
        self,
        parsed: ParsedSearchQuery,
        *,
        tenant_id: str,
        config: SearchQueryConfig | None = None,
        size: int = 50,
        offset: int = 0,
        search_after: list[Any] | None = None,
        sort_mode: str = "relevance",
    ) -> dict[str, Any]:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if (
            size < 1
            or size > 1_000
            or offset < 0
            or offset > _MAX_OFFSET
            or (search_after is not None and offset)
        ):
            raise ValueError("invalid search pagination")
        sort = self._sort(sort_mode)
        query_config = config or SearchQueryConfig()
        try:
            clauses = [self._clause(item, query_config) for item in parsed.clauses]
        except ValueError:
            clauses = [
                self._plain_term(item.value, query_config)
                for item in parsed.clauses
            ]

        content: dict[str, Any] | None
        if not clauses:
            content = None
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

        query_bool: dict[str, Any] = {
            "filter": [{"term": {"tenant_id": tenant_id}}],
        }
        if content is not None:
            query_bool["must"] = [content]
        document: dict[str, Any] = {
            "size": size,
            "sort": sort,
            "_source": ["asset_id", "source_id", "filename", "folder_path"],
            "query": {"bool": query_bool},
        }
        if search_after is None:
            document["from"] = offset
        else:
            document["search_after"] = search_after
        return document

    @staticmethod
    def _sort(sort_mode: str) -> list[dict[str, Any]]:
        sorts: dict[str, list[dict[str, Any]]] = {
            "relevance": [{"_score": "desc"}, {"asset_id": "asc"}],
            "newest": [
                {"source_modified_at": {"order": "desc", "missing": "_last"}},
                {"asset_id": "asc"},
            ],
            "oldest": [
                {"source_modified_at": {"order": "asc", "missing": "_last"}},
                {"asset_id": "asc"},
            ],
            "name_asc": [
                {"filename.normalized": {"order": "asc", "missing": "_last"}},
                {"asset_id": "asc"},
            ],
            "name_desc": [
                {"filename.normalized": {"order": "desc", "missing": "_last"}},
                {"asset_id": "asc"},
            ],
        }
        try:
            return sorts[sort_mode]
        except KeyError as exc:
            raise ValueError("unsupported search sort") from exc

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
        exact: list[dict[str, Any]] = [
            {
                "term": {
                    "filename.normalized": {
                        "value": value,
                        "boost": self.EXACT_FILENAME_BOOST,
                    }
                }
            },
            {
                "term": {
                    "search_terms": {
                        "value": value,
                        "boost": self.EXACT_TERM_BOOST,
                    }
                }
            },
            {
                "term": {
                    "normalized_terms": {
                        "value": value,
                        "boost": self.NORMALIZED_TERM_BOOST,
                    }
                }
            },
            *self._boosted_paths(value, config),
        ]
        if value.isdigit():
            exact.insert(
                0,
                {
                    "term": {
                        "numbers": {
                            "value": value,
                            "boost": self.EXACT_NUMBER_BOOST,
                        }
                    }
                },
            )
        lexical = {
            "dis_max": {
                "tie_breaker": 0.1,
                "queries": [
                    {"match": {"visible_text": {"query": value, "boost": self.VISIBLE_TEXT_BOOST}}},
                    {"match": {"filename": {"query": value, "boost": self.FILENAME_BOOST}}},
                    {"match": {"search_text": {"query": value, "boost": self.SEARCH_TEXT_BOOST}}},
                    {"match": {"folder_path": {"query": value, "boost": self.FOLDER_BOOST}}},
                ],
            }
        }
        should: list[dict[str, Any]] = [*exact, lexical]
        if len(value) >= 2:
            should.append({
                "dis_max": {
                    "tie_breaker": 0.05,
                    "queries": [
                        {"match_phrase_prefix": {"visible_text": {"query": value, "boost": self.PREFIX_BOOST}}},
                        {"match_phrase_prefix": {"filename": {"query": value, "boost": self.PREFIX_BOOST}}},
                        {"match_phrase_prefix": {"search_text": {"query": value, "boost": self.PREFIX_BOOST}}},
                        {
                            "multi_match": {
                                "query": value,
                                "type": "bool_prefix",
                                "fields": [
                                    "search_suggest",
                                    "search_suggest._2gram",
                                    "search_suggest._3gram",
                                ],
                                "boost": self.PREFIX_BOOST,
                            }
                        },
                    ],
                }
            })
        if len(value) >= self.FUZZY_MIN_LENGTH:
            should.append({
                "multi_match": {
                    "query": value,
                    "type": "best_fields",
                    "fields": ["visible_text^3", "filename^2", "search_text"],
                    "fuzziness": "AUTO",
                    "prefix_length": 1,
                    "max_expansions": self.FUZZY_MAX_EXPANSIONS,
                    "boost": self.FUZZY_BOOST,
                }
            })
        return {"bool": {"should": should, "minimum_should_match": 1}}

    def _phrase(self, value: str, config: SearchQueryConfig) -> dict[str, Any]:
        return {
            "bool": {
                "should": [
                    {
                        "term": {
                            "filename.normalized": {
                                "value": value,
                                "boost": self.EXACT_FILENAME_BOOST,
                            }
                        }
                    },
                    {
                        "term": {
                            "phrases": {
                                "value": value,
                                "boost": self.EXACT_PHRASE_BOOST,
                            }
                        }
                    },
                    *self._boosted_paths(value, config),
                    {
                        "dis_max": {
                            "tie_breaker": 0.1,
                            "queries": [
                                {"match_phrase": {"visible_text": {"query": value, "boost": self.VISIBLE_TEXT_BOOST}}},
                                {"match_phrase": {"filename": {"query": value, "boost": self.FILENAME_BOOST}}},
                                {"match_phrase": {"search_text": {"query": value, "boost": self.SEARCH_TEXT_BOOST}}},
                                {"match_phrase": {"folder_path": {"query": value, "boost": self.FOLDER_BOOST}}},
                            ],
                        }
                    },
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
            return {
                "term": {
                    f"facets.{name}": {
                        "value": value,
                        "boost": self.EXACT_TERM_BOOST,
                    }
                }
            }
        if target.startswith("path:"):
            return self._nested_path(
                target.removeprefix("path:"),
                value,
                self.BOOSTED_PATH_BOOST,
            )
        analyzed = {"search_text", "filename", "folder_path"}
        keywords = {"numbers", "search_terms", "normalized_terms", "phrases"}
        if target not in analyzed | keywords:
            raise ValueError("unsafe qualified field")
        if target in analyzed:
            query_kind = (
                "match_phrase"
                if kind is ClauseKind.QUALIFIED_PHRASE
                else "match"
            )
            return {
                query_kind: {
                    target: {
                        "query": value,
                        "boost": (
                            self.EXACT_PHRASE_BOOST
                            if query_kind == "match_phrase"
                            else self.EXACT_TERM_BOOST
                        ),
                    }
                }
            }
        return {
            "term": {
                target: {"value": value, "boost": self.EXACT_TERM_BOOST}
            }
        }

    def _boosted_paths(
        self, value: str, config: SearchQueryConfig
    ) -> list[dict[str, Any]]:
        return [
            self._nested_path(
                path,
                value,
                self.BOOSTED_PATH_BOOST * min(boost, 1.0),
            )
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
