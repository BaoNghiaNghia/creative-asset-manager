from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from app.modules.ai_metadata.traverser import ExtractedMetadataValue

_INTEGER_TOKEN_RE = re.compile(r"^\d+$")


@dataclass(frozen=True, slots=True)
class NormalizedMetadataValue:
    path: str
    original_value: str
    normalized_value: str
    tokens: tuple[str, ...]
    numbers: tuple[str, ...]
    phrases: tuple[str, ...]


class MetadataNormalizer:
    def __init__(
        self,
        *,
        max_values: int = 10_000,
        max_terms_per_value: int = 256,
        max_phrases_per_value: int = 1,
        max_phrase_chars: int = 512,
    ):
        limits = {
            "max_values": max_values,
            "max_terms_per_value": max_terms_per_value,
            "max_phrases_per_value": max_phrases_per_value,
            "max_phrase_chars": max_phrase_chars,
        }
        for name, value in limits.items():
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
        self.max_values = max_values
        self.max_terms_per_value = max_terms_per_value
        self.max_phrases_per_value = max_phrases_per_value
        self.max_phrase_chars = max_phrase_chars

    def normalize(
        self, value: ExtractedMetadataValue
    ) -> NormalizedMetadataValue | None:
        normalized = self.normalize_text(value.original_value)
        if not normalized:
            return None

        tokens = self._unique(normalized.split())[: self.max_terms_per_value]
        numbers = tuple(token for token in tokens if _INTEGER_TOKEN_RE.fullmatch(token))
        phrases: tuple[str, ...] = ()
        if len(tokens) > 1:
            phrase = " ".join(tokens)
            if len(phrase) <= self.max_phrase_chars:
                phrases = (phrase,)[: self.max_phrases_per_value]

        return NormalizedMetadataValue(
            path=value.path,
            original_value=value.original_value,
            normalized_value=normalized,
            tokens=tokens,
            numbers=numbers,
            phrases=phrases,
        )

    def normalize_all(
        self, values: Iterable[ExtractedMetadataValue]
    ) -> tuple[NormalizedMetadataValue, ...]:
        normalized = [
            item
            for value in values
            if (item := self.normalize(value)) is not None
        ]
        return tuple(
            sorted(
                normalized,
                key=lambda item: (
                    item.path,
                    item.normalized_value,
                    item.original_value,
                ),
            )[: self.max_values]
        )

    @staticmethod
    def normalize_text(value: str) -> str:
        folded = unicodedata.normalize("NFKC", value).casefold()
        characters: list[str] = []
        for character in folded:
            category = unicodedata.category(character)
            if category[0] in {"L", "N", "M"}:
                characters.append(character)
            else:
                characters.append(" ")
        return " ".join("".join(characters).split())

    @staticmethod
    def _unique(values: Iterable[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return tuple(result)
