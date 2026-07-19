from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Mapping

from jsonschema import SchemaError
from jsonschema.validators import validator_for


@dataclass(frozen=True, slots=True)
class MetadataValidationError:
    code: str
    message: str
    path: tuple[str | int, ...] = ()
    limit: int | None = None
    actual: int | None = None


@dataclass(frozen=True, slots=True)
class MetadataValidationResult:
    valid: bool
    document: dict[str, Any] | None
    errors: tuple[MetadataValidationError, ...]


class MetadataDocumentValidator:
    def __init__(
        self,
        *,
        max_bytes: int = 1_000_000,
        max_depth: int = 20,
        max_nodes: int = 50_000,
        max_array_items: int = 10_000,
        max_string_length: int = 100_000,
        max_schema_errors: int = 100,
    ):
        self.max_bytes = max_bytes
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_array_items = max_array_items
        self.max_string_length = max_string_length
        self.max_schema_errors = max_schema_errors

    def validate(
        self,
        value: str | bytes | Mapping[str, Any],
        *,
        json_schema: Mapping[str, Any] | None = None,
    ) -> MetadataValidationResult:
        parsed, byte_size, parse_error = self._parse(value)
        if parse_error is not None:
            return MetadataValidationResult(False, None, (parse_error,))
        if byte_size > self.max_bytes:
            return self._failure("max_bytes", "metadata document exceeds byte limit", self.max_bytes, byte_size)
        if not isinstance(parsed, dict):
            return MetadataValidationResult(
                False,
                None,
                (MetadataValidationError("root_object", "metadata root must be a JSON object"),),
            )
        safe_document = copy.deepcopy(parsed)
        safety_error = self._validate_structure(safe_document)
        if safety_error is not None:
            return MetadataValidationResult(False, None, (safety_error,))
        if json_schema is not None:
            errors = self._validate_schema(safe_document, json_schema)
            if errors:
                return MetadataValidationResult(False, None, errors)
        return MetadataValidationResult(True, safe_document, ())

    def _parse(
        self, value: str | bytes | Mapping[str, Any]
    ) -> tuple[Any, int, MetadataValidationError | None]:
        try:
            if isinstance(value, bytes):
                raw = value
                parsed = json.loads(
                    value.decode("utf-8"),
                    parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
                )
            elif isinstance(value, str):
                raw = value.encode("utf-8")
                parsed = json.loads(
                    value,
                    parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
                )
            elif isinstance(value, Mapping):
                parsed = copy.deepcopy(dict(value))
                raw = json.dumps(
                    parsed, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            else:
                return None, 0, MetadataValidationError(
                    "invalid_json", "metadata must be JSON text, bytes, or an object"
                )
            return parsed, len(raw), None
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
            return None, 0, MetadataValidationError("invalid_json", f"invalid JSON: {exc}")

    def _validate_structure(self, document: dict[str, Any]) -> MetadataValidationError | None:
        stack: list[tuple[Any, int, tuple[str | int, ...]]] = [(document, 1, ())]
        nodes = 0
        while stack:
            node, depth, path = stack.pop()
            nodes += 1
            if nodes > self.max_nodes:
                return MetadataValidationError(
                    "max_nodes", "metadata node limit exceeded", path, self.max_nodes, nodes
                )
            if depth > self.max_depth:
                return MetadataValidationError(
                    "max_depth", "metadata depth limit exceeded", path, self.max_depth, depth
                )
            if isinstance(node, dict):
                for key, child in node.items():
                    if not isinstance(key, str):
                        return MetadataValidationError(
                            "invalid_key", "metadata object keys must be strings", path
                        )
                    nodes += 1
                    if nodes > self.max_nodes:
                        return MetadataValidationError(
                            "max_nodes", "metadata node limit exceeded", path, self.max_nodes, nodes
                        )
                    if len(key) > self.max_string_length:
                        return MetadataValidationError(
                            "max_string_length", "metadata key exceeds string limit",
                            path + (key,), self.max_string_length, len(key),
                        )
                    stack.append((child, depth + 1, path + (key,)))
            elif isinstance(node, list):
                if len(node) > self.max_array_items:
                    return MetadataValidationError(
                        "max_array_items", "metadata array item limit exceeded",
                        path, self.max_array_items, len(node),
                    )
                for index in range(len(node) - 1, -1, -1):
                    stack.append((node[index], depth + 1, path + (index,)))
            elif isinstance(node, str) and len(node) > self.max_string_length:
                return MetadataValidationError(
                    "max_string_length", "metadata string exceeds length limit",
                    path, self.max_string_length, len(node),
                )
            elif node is not None and not isinstance(node, (str, int, float, bool)):
                return MetadataValidationError(
                    "invalid_json_type", "metadata contains a non-JSON value", path
                )
        return None

    def _validate_schema(
        self, document: dict[str, Any], json_schema: Mapping[str, Any]
    ) -> tuple[MetadataValidationError, ...]:
        try:
            validator_class = validator_for(json_schema)
            validator_class.check_schema(json_schema)
            validator = validator_class(json_schema)
        except SchemaError as exc:
            return (MetadataValidationError("invalid_schema", str(exc)),)
        errors = sorted(
            validator.iter_errors(document),
            key=lambda item: tuple(str(part) for part in item.path),
        )
        return tuple(
            MetadataValidationError(
                "json_schema",
                error.message,
                tuple(error.absolute_path),
            )
            for error in errors[: self.max_schema_errors]
        )

    @staticmethod
    def _failure(code: str, message: str, limit: int, actual: int) -> MetadataValidationResult:
        return MetadataValidationResult(
            False, None, (MetadataValidationError(code, message, (), limit, actual),)
        )
