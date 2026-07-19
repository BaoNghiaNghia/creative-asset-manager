from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

MetadataValueType = Literal["string", "number", "boolean"]

_ARRAY_INDEX_RE = re.compile(r"\[\d+\]")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_URL_RE = re.compile(r"(?i)^(?:https?|ftp)://|^www\.")
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


@dataclass(frozen=True, slots=True)
class ExtractedMetadataValue:
    path: str
    original_value: str
    value_type: MetadataValueType


@dataclass(frozen=True, slots=True)
class TraversalLimits:
    max_depth: int = 20
    max_nodes: int = 50_000
    max_array_items: int = 10_000
    max_extracted_values: int = 10_000

    def __post_init__(self) -> None:
        for name in (
            "max_depth",
            "max_nodes",
            "max_array_items",
            "max_extracted_values",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")


def normalize_logical_path(path: str) -> str:
    without_indexes = _ARRAY_INDEX_RE.sub("", unicodedata.normalize("NFKC", path))
    return ".".join(part.strip() for part in without_indexes.split(".") if part.strip())


class MetadataTraverser:
    _EXCLUDED_SEGMENTS = frozenset(
        {
            "access_token",
            "auth",
            "authentication",
            "authorization",
            "base64",
            "bbox",
            "bounding_box",
            "bounding_boxes",
            "coordinate",
            "coordinates",
            "credential",
            "credentials",
            "debug",
            "debug_payload",
            "embedding",
            "embeddings",
            "id_token",
            "password",
            "provider_request_id",
            "raw_response",
            "refresh_token",
            "request_id",
            "secret",
            "signed_url",
            "token",
            "vector",
            "vectors",
        }
    )
    _URL_SEGMENTS = frozenset({"href", "uri", "url"})

    def __init__(
        self,
        *,
        include_booleans: bool = False,
        limits: TraversalLimits | None = None,
        global_exclude_paths: Sequence[str] = (),
    ):
        self.include_booleans = include_booleans
        self.limits = limits or TraversalLimits()
        self.global_exclude_paths = tuple(
            self._canonical_path(path)
            for path in global_exclude_paths
            if isinstance(path, str) and normalize_logical_path(path)
        )

    def traverse(
        self,
        document: Mapping[str, Any],
        *,
        exclude_paths: Sequence[str] = (),
        include_booleans: bool | None = None,
    ) -> tuple[ExtractedMetadataValue, ...]:
        if not isinstance(document, Mapping):
            raise TypeError("metadata document must be an object")

        configured_excludes = self.global_exclude_paths + tuple(
            self._canonical_path(path)
            for path in exclude_paths
            if isinstance(path, str) and normalize_logical_path(path)
        )
        extract_booleans = (
            self.include_booleans if include_booleans is None else include_booleans
        )
        stack: list[tuple[Any, str, int]] = [(document, "", 0)]
        visited_containers: set[int] = set()
        extracted: list[ExtractedMetadataValue] = []
        visited_nodes = 0

        while stack and len(extracted) < self.limits.max_extracted_values:
            value, path, depth = stack.pop()
            visited_nodes += 1
            if visited_nodes > self.limits.max_nodes:
                break
            if depth > self.limits.max_depth or self._is_excluded_path(
                path, configured_excludes
            ):
                continue

            if isinstance(value, Mapping):
                identity = id(value)
                if identity in visited_containers:
                    continue
                visited_containers.add(identity)
                keys = sorted((key for key in value if isinstance(key, str)), reverse=True)
                for key in keys:
                    child_path = f"{path}.{key}" if path else key
                    stack.append((value[key], child_path, depth + 1))
                continue

            if isinstance(value, list):
                identity = id(value)
                if identity in visited_containers:
                    continue
                visited_containers.add(identity)
                bounded = value[: self.limits.max_array_items]
                for item in reversed(bounded):
                    stack.append((item, path, depth + 1))
                continue

            item = self._extract_scalar(path, value, extract_booleans)
            if item is not None and not self._is_sensitive_value(item.original_value):
                extracted.append(item)

        return tuple(
            sorted(
                extracted,
                key=lambda item: (item.path, item.value_type, item.original_value),
            )
        )

    def _extract_scalar(
        self, path: str, value: Any, include_booleans: bool
    ) -> ExtractedMetadataValue | None:
        logical_path = normalize_logical_path(path)
        if not logical_path or value is None:
            return None
        if isinstance(value, bool):
            if not include_booleans:
                return None
            return ExtractedMetadataValue(
                logical_path, "true" if value else "false", "boolean"
            )
        if isinstance(value, str):
            return ExtractedMetadataValue(logical_path, value, "string")
        if isinstance(value, int):
            return ExtractedMetadataValue(logical_path, str(value), "number")
        if isinstance(value, float) and math.isfinite(value):
            return ExtractedMetadataValue(
                logical_path,
                json.dumps(value, allow_nan=False, separators=(",", ":")),
                "number",
            )
        return None

    @classmethod
    def _is_excluded_path(
        cls, path: str, configured_excludes: Sequence[str]
    ) -> bool:
        canonical = cls._canonical_path(path)
        if not canonical:
            return False
        if any(
            canonical == excluded or canonical.startswith(f"{excluded}.")
            for excluded in configured_excludes
        ):
            return True
        segments = canonical.split(".")
        for segment in segments:
            if segment in cls._EXCLUDED_SEGMENTS:
                return True
            if segment in cls._URL_SEGMENTS or segment.endswith("_url"):
                return True
            if segment.endswith(
                (
                    "_base64",
                    "_bounding_box",
                    "_bounding_boxes",
                    "_coordinate",
                    "_coordinates",
                    "_debug",
                    "_debug_payload",
                    "_embedding",
                    "_embeddings",
                    "_request_id",
                    "_token",
                    "_vector",
                    "_vectors",
                )
            ):
                return True
        return False

    @staticmethod
    def _is_sensitive_value(value: str) -> bool:
        stripped = value.strip()
        if not stripped:
            return False
        if _URL_RE.search(stripped) or stripped.casefold().startswith("data:"):
            return True
        if stripped.casefold().startswith(("bearer ", "basic ")):
            return True
        compact = "".join(stripped.split())
        if _JWT_RE.fullmatch(compact):
            return True
        return (
            len(compact) >= 80
            and len(compact) % 4 == 0
            and _BASE64_RE.fullmatch(compact) is not None
        )

    @staticmethod
    def _canonical_path(path: str) -> str:
        logical = normalize_logical_path(path)
        canonical_parts: list[str] = []
        for part in logical.split("."):
            snake = _CAMEL_BOUNDARY_RE.sub("_", part)
            canonical = _NON_WORD_RE.sub("_", snake.casefold()).strip("_")
            if canonical:
                canonical_parts.append(canonical)
        return ".".join(canonical_parts)
