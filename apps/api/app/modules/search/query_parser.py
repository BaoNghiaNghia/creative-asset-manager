from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from app.modules.ai_metadata.normalizer import MetadataNormalizer


class QueryMode(str, Enum):
    SINGLE = "single"
    SOFT_AND = "soft_and"
    STRICT_AND = "strict_and"
    OR = "or"
    FALLBACK = "fallback"


class ClauseKind(str, Enum):
    TERM = "term"
    PHRASE = "phrase"
    QUALIFIED_TERM = "qualified_term"
    QUALIFIED_PHRASE = "qualified_phrase"


@dataclass(frozen=True, slots=True)
class SearchClause:
    kind: ClauseKind
    value: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedSearchQuery:
    raw: str
    mode: QueryMode
    clauses: tuple[SearchClause, ...]


class SearchQueryParser:
    def parse(self, raw: str) -> ParsedSearchQuery:
        query = raw.strip()
        if not query:
            return ParsedSearchQuery(raw, QueryMode.FALLBACK, ())
        try:
            or_parts = self._split_operator(query, "OR")
            if len(or_parts) > 1:
                return self._build(raw, QueryMode.OR, or_parts)
            comma_parts = self._split_commas(query)
            if len(comma_parts) > 1:
                return self._build(raw, QueryMode.STRICT_AND, comma_parts)
            clauses = self._tokenize(query)
            mode = QueryMode.SINGLE if len(clauses) == 1 else QueryMode.SOFT_AND
            return ParsedSearchQuery(raw, mode, tuple(clauses))
        except ValueError:
            normalized = MetadataNormalizer.normalize_text(query)
            clauses = tuple(
                SearchClause(ClauseKind.TERM, token)
                for token in normalized.split()
            )
            return ParsedSearchQuery(raw, QueryMode.FALLBACK, clauses)

    def _build(
        self, raw: str, mode: QueryMode, parts: Sequence[str]
    ) -> ParsedSearchQuery:
        clauses: list[SearchClause] = []
        for part in parts:
            parsed = self._tokenize(part)
            if len(parsed) != 1:
                raise ValueError("operators require one clause per side")
            clauses.extend(parsed)
        return ParsedSearchQuery(raw, mode, tuple(clauses))

    def _tokenize(self, value: str) -> list[SearchClause]:
        tokens: list[str] = []
        buffer: list[str] = []
        quoted = False
        for character in value.strip():
            if character == '"':
                quoted = not quoted
                buffer.append(character)
            elif character.isspace() and not quoted:
                if buffer:
                    tokens.append("".join(buffer))
                    buffer = []
            else:
                buffer.append(character)
        if quoted:
            raise ValueError("unterminated quote")
        if buffer:
            tokens.append("".join(buffer))
        if not tokens:
            raise ValueError("empty query")
        return [self._clause(token) for token in tokens]

    def _clause(self, token: str) -> SearchClause:
        field: str | None = None
        value = token
        if ":" in token:
            field, value = token.split(":", 1)
            if not field or not field.replace("_", "").isalnum() or not value:
                raise ValueError("invalid qualifier")
        quoted = value.startswith('"') or value.endswith('"')
        if quoted:
            if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
                raise ValueError("invalid phrase")
            value = value[1:-1]
        normalized = MetadataNormalizer.normalize_text(value)
        if not normalized:
            raise ValueError("empty clause")
        if field:
            kind = ClauseKind.QUALIFIED_PHRASE if quoted else ClauseKind.QUALIFIED_TERM
            return SearchClause(kind, normalized, field.casefold())
        kind = ClauseKind.PHRASE if quoted else ClauseKind.TERM
        return SearchClause(kind, normalized)

    @staticmethod
    def _split_commas(value: str) -> list[str]:
        return SearchQueryParser._split(value, comma=True)

    @staticmethod
    def _split_operator(value: str, operator: str) -> list[str]:
        return SearchQueryParser._split(value, operator=operator)

    @staticmethod
    def _split(
        value: str, *, comma: bool = False, operator: str | None = None
    ) -> list[str]:
        parts: list[str] = []
        buffer: list[str] = []
        quoted = False
        index = 0
        while index < len(value):
            character = value[index]
            if character == '"':
                quoted = not quoted
                buffer.append(character)
                index += 1
                continue
            is_separator = comma and character == "," and not quoted
            if operator and not quoted:
                candidate = value[index : index + len(operator)]
                before_ok = index == 0 or value[index - 1].isspace()
                after = index + len(operator)
                after_ok = after <= len(value) and (
                    after == len(value) or value[after].isspace()
                )
                is_separator = candidate.upper() == operator and before_ok and after_ok
            if is_separator:
                part = "".join(buffer).strip()
                if not part:
                    raise ValueError("empty operator operand")
                parts.append(part)
                buffer = []
                index += len(operator) if operator else 1
                continue
            buffer.append(character)
            index += 1
        if quoted:
            raise ValueError("unterminated quote")
        part = "".join(buffer).strip()
        if not part and parts:
            raise ValueError("empty operator operand")
        if part:
            parts.append(part)
        return parts
