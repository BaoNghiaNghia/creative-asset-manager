from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

import openai
from openai import AsyncOpenAI

from app.domain.providers.contracts import (
    AiBatchResult,
    AiBatchResultsInput,
    AiBatchStatus,
    AiBatchStatusInput,
    AiBatchSubmission,
    AiBatchSubmissionInput,
    AiMetadataAnalysisInput,
    AiMetadataAnalysisResult,
    AiProviderError,
)

_SCHEMA_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
_SUPPORTED_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


class OpenAiMetadataProvider:
    """OpenAI Responses API adapter for bounded single-image metadata analysis."""

    provider_name = "openai"
    supports_single = True
    supports_batch = False

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        allowed_models: tuple[str, ...],
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        image_detail: str = "auto",
        store_responses: bool = False,
        organization: str | None = None,
        project: str | None = None,
        capture_raw_response: bool = False,
        max_image_bytes: int = 8_000_000,
        client: Any | None = None,
    ):
        if not api_key:
            raise ValueError("OpenAI API key is required")
        normalized_model = model.strip()
        normalized_allowed = tuple(
            value.strip() for value in allowed_models if value.strip()
        )
        if not normalized_model:
            raise ValueError("OpenAI model is required")
        if normalized_model not in normalized_allowed:
            raise ValueError("OpenAI model is not in the configured allowlist")
        if timeout_seconds <= 0:
            raise ValueError("OpenAI timeout must be positive")
        if max_retries < 0:
            raise ValueError("OpenAI max retries cannot be negative")
        if image_detail not in {"auto", "low", "high", "original"}:
            raise ValueError("OpenAI image detail is invalid")
        if max_image_bytes <= 0:
            raise ValueError("OpenAI image byte limit must be positive")

        self.model = normalized_model
        self.default_model = normalized_model
        self.allowed_models = frozenset(normalized_allowed)
        self.timeout_seconds = float(timeout_seconds)
        self.image_detail = image_detail
        self.store_responses = bool(store_responses)
        self.capture_raw_response = bool(capture_raw_response)
        self.max_image_bytes = int(max_image_bytes)
        if client is None:
            options: dict[str, Any] = {
                "api_key": api_key,
                "timeout": self.timeout_seconds,
                "max_retries": max_retries,
            }
            if base_url:
                options["base_url"] = base_url
            if organization:
                options["organization"] = organization
            if project:
                options["project"] = project
            client = AsyncOpenAI(**options)
        self._client = client

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(model={self.model!r}, "
            f"store_responses={self.store_responses!r})"
        )

    async def analyze_single(
        self, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult:
        self._check_cancelled(input)
        if self.model not in self.allowed_models:
            raise AiProviderError(
                "The configured OpenAI model is not allowed.",
                code="openai_model_not_allowed",
                retryable=False,
            )
        if input.image_mime_type not in _SUPPORTED_IMAGE_TYPES:
            raise AiProviderError(
                "The prepared image type is not supported by OpenAI.",
                code="openai_unsupported_image",
                retryable=False,
            )
        if not input.image_bytes or len(input.image_bytes) > self.max_image_bytes:
            raise AiProviderError(
                "The prepared image exceeds the OpenAI input limit.",
                code="openai_image_too_large",
                retryable=False,
            )

        schema = self._openai_schema(input.json_schema)
        text_format: dict[str, Any]
        prompt = input.prompt
        if schema is not None:
            text_format = {
                "type": "json_schema",
                "name": self._schema_name(
                    input.metadata_profile,
                    input.metadata_profile_version,
                ),
                "strict": True,
                "schema": schema,
            }
        else:
            text_format = {"type": "json_object"}
            prompt = (
                f"{prompt}\n\n"
                "Return exactly one JSON object and no surrounding commentary."
            )

        encoded = base64.b64encode(input.image_bytes).decode("ascii")
        request = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": (
                                f"data:{input.image_mime_type};base64,{encoded}"
                            ),
                            "detail": self.image_detail,
                        },
                    ],
                }
            ],
            "text": {"format": text_format},
            "store": self.store_responses,
            "timeout": self.timeout_seconds,
        }

        started = time.monotonic()
        try:
            call = self._client.responses.create(**request)
            response = await asyncio.wait_for(call, timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise self._error(
                "OpenAI request timed out.",
                "openai_timeout",
                True,
                exc,
            )
        except openai.APITimeoutError as exc:
            raise self._error(
                "OpenAI request timed out.",
                "openai_timeout",
                True,
                exc,
            )
        except openai.RateLimitError as exc:
            raise self._error(
                "OpenAI rate limit was reached.",
                "openai_rate_limit",
                True,
                exc,
            )
        except openai.AuthenticationError as exc:
            raise self._error(
                "OpenAI authentication failed.",
                "openai_authentication_failed",
                False,
                exc,
            )
        except openai.APIConnectionError as exc:
            raise self._error(
                "OpenAI could not be reached.",
                "openai_connection_error",
                True,
                exc,
            )
        except openai.APIStatusError as exc:
            raise self._status_error(exc)
        except openai.OpenAIError as exc:
            raise self._error(
                "OpenAI rejected the request.",
                "openai_request_failed",
                False,
                exc,
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        self._check_cancelled(input)
        text, refusal = self._response_text(response)
        status = str(getattr(response, "status", "") or "").lower()
        incomplete = self._object_dict(
            getattr(response, "incomplete_details", None)
        )
        incomplete_reason = (
            str(incomplete.get("reason")) if incomplete.get("reason") else None
        )
        if refusal:
            raise AiProviderError(
                "OpenAI refused the metadata request.",
                code="openai_refusal",
                retryable=False,
            )
        if status and status != "completed":
            raise AiProviderError(
                "OpenAI returned an incomplete response.",
                code="openai_incomplete_response",
                retryable=True,
            )
        if not text or not text.strip():
            raise AiProviderError(
                "OpenAI returned an empty response.",
                code="openai_empty_response",
                retryable=True,
            )
        try:
            metadata = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AiProviderError(
                "OpenAI returned malformed JSON.",
                code="openai_invalid_json",
                retryable=True,
            ) from exc
        if not isinstance(metadata, Mapping):
            raise AiProviderError(
                "OpenAI metadata root must be an object.",
                code="openai_invalid_document",
                retryable=True,
            )

        usage_payload = self._object_dict(getattr(response, "usage", None))
        usage = self._usage(usage_payload)
        response_id = getattr(response, "id", None)
        request_id = getattr(response, "_request_id", None) or response_id
        response_model = getattr(response, "model", None) or self.model
        raw_response = (
            self._object_dict(response) if self.capture_raw_response else None
        )
        return AiMetadataAnalysisResult(
            metadata=dict(metadata),
            provider=self.provider_name,
            model=str(response_model),
            provider_request_id=str(request_id) if request_id else None,
            usage=usage,
            provider_metadata={
                "response_id": str(response_id) if response_id else None,
                "status": status or None,
                "incomplete_reason": incomplete_reason,
                "latency_ms": latency_ms,
                "structured_output": schema is not None,
                "stored_by_provider": self.store_responses,
            },
            raw_response=raw_response,
        )

    async def submit_batch(
        self, input: AiBatchSubmissionInput
    ) -> AiBatchSubmission:
        raise self._batch_not_implemented()

    async def get_batch_status(
        self, input: AiBatchStatusInput
    ) -> AiBatchStatus:
        raise self._batch_not_implemented()

    async def stream_batch_results(
        self, input: AiBatchResultsInput
    ) -> AsyncIterator[AiBatchResult]:
        raise self._batch_not_implemented()
        if False:
            yield AiBatchResult(custom_item_id="unreachable")

    async def cancel_batch(self, input: AiBatchStatusInput) -> bool:
        raise self._batch_not_implemented()

    async def aclose(self) -> None:
        closer = getattr(self._client, "close", None)
        if closer is None:
            return
        result = closer()
        if hasattr(result, "__await__"):
            await result

    @staticmethod
    def _batch_not_implemented() -> AiProviderError:
        return AiProviderError(
            "OpenAI batch analysis is not implemented.",
            code="openai_batch_not_implemented",
            retryable=False,
        )

    @staticmethod
    def _check_cancelled(input: AiMetadataAnalysisInput) -> None:
        if input.is_cancelled is not None and input.is_cancelled():
            raise AiProviderError(
                "OpenAI analysis was cancelled.",
                code="analysis_cancelled",
                retryable=True,
            )

    @staticmethod
    def _error(
        message: str,
        code: str,
        retryable: bool,
        cause: Exception,
    ) -> AiProviderError:
        error = AiProviderError(message, code=code, retryable=retryable)
        error.__cause__ = cause
        return error

    @classmethod
    def _status_error(cls, exc: openai.APIStatusError) -> AiProviderError:
        status = int(getattr(exc, "status_code", 0) or 0)
        hint = cls._error_hint(exc)
        if status in {408, 409} or status >= 500:
            return cls._error(
                "OpenAI is temporarily unavailable.",
                "openai_service_unavailable",
                True,
                exc,
            )
        if status == 429:
            return cls._error(
                "OpenAI rate limit was reached.",
                "openai_rate_limit",
                True,
                exc,
            )
        if status in {401, 403}:
            return cls._error(
                "OpenAI authentication failed.",
                "openai_authentication_failed",
                False,
                exc,
            )
        if "schema" in hint or "json_schema" in hint:
            code = "openai_invalid_schema"
            message = "OpenAI rejected the metadata JSON Schema."
        elif "image" in hint or "image_url" in hint:
            code = "openai_unsupported_image"
            message = "OpenAI rejected the prepared image."
        else:
            code = "openai_invalid_request"
            message = "OpenAI rejected the metadata request."
        return cls._error(message, code, False, exc)

    @classmethod
    def _error_hint(cls, exc: Exception) -> str:
        body = getattr(exc, "body", None)
        if isinstance(body, Mapping):
            try:
                return json.dumps(body, ensure_ascii=True).lower()
            except (TypeError, ValueError):
                return ""
        return str(exc).lower()

    @classmethod
    def _response_text(cls, response: Any) -> tuple[str, str | None]:
        texts: list[str] = []
        refusal: str | None = None
        for output in getattr(response, "output", None) or ():
            for content in getattr(output, "content", None) or ():
                content_type = getattr(content, "type", None)
                if content_type == "refusal":
                    value = getattr(content, "refusal", None)
                    refusal = str(value or "refused")
                elif content_type == "output_text":
                    value = getattr(content, "text", None)
                    if isinstance(value, str):
                        texts.append(value)
        if not texts:
            try:
                value = response.output_text
            except (AttributeError, TypeError):
                value = None
            if isinstance(value, str):
                texts.append(value)
        return "".join(texts), refusal

    @staticmethod
    def _usage(payload: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for source, target in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = payload.get(source)
            if isinstance(value, (int, float)):
                result[target] = max(0, int(value))
        input_details = payload.get("input_tokens_details")
        output_details = payload.get("output_tokens_details")
        if isinstance(input_details, Mapping):
            result["input_tokens_details"] = dict(input_details)
        if isinstance(output_details, Mapping):
            result["output_tokens_details"] = dict(output_details)
        return result

    @staticmethod
    def _object_dict(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return dict(value)
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            try:
                result = dump(mode="json", exclude_none=True)
            except TypeError:
                result = dump()
            return dict(result) if isinstance(result, Mapping) else {}
        return {}

    @classmethod
    def _openai_schema(
        cls, schema: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        if not isinstance(schema, Mapping):
            return None
        candidate = copy.deepcopy(dict(schema))
        if candidate.get("type") != "object" or "anyOf" in candidate:
            return None
        counts = {"properties": 0}
        if not cls._normalize_schema_node(candidate, depth=1, counts=counts):
            return None
        return candidate

    @classmethod
    def _normalize_schema_node(
        cls,
        node: Any,
        *,
        depth: int,
        counts: dict[str, int],
    ) -> bool:
        if not isinstance(node, dict) or depth > 10:
            return False
        node_type = node.get("type")
        if node_type == "object":
            properties = node.get("properties")
            if not isinstance(properties, dict):
                return False
            counts["properties"] += len(properties)
            if counts["properties"] > 5000:
                return False
            required = node.get("required")
            if not isinstance(required, list) or set(required) != set(properties):
                return False
            additional = node.get("additionalProperties")
            if additional is not None and additional is not False:
                return False
            node["additionalProperties"] = False
            for child in properties.values():
                if not cls._normalize_schema_node(
                    child, depth=depth + 1, counts=counts
                ):
                    return False
        elif node_type == "array":
            if not cls._normalize_schema_node(
                node.get("items"), depth=depth + 1, counts=counts
            ):
                return False
        for keyword in ("anyOf", "oneOf"):
            options = node.get(keyword)
            if options is not None:
                if not isinstance(options, list) or not options:
                    return False
                for child in options:
                    if not cls._normalize_schema_node(
                        child, depth=depth + 1, counts=counts
                    ):
                        return False
        definitions = node.get("$defs")
        if definitions is not None:
            if not isinstance(definitions, dict):
                return False
            for child in definitions.values():
                if not cls._normalize_schema_node(
                    child, depth=depth + 1, counts=counts
                ):
                    return False
        return True

    @staticmethod
    def _schema_name(profile: str, version: str) -> str:
        source = f"{profile}_{version}"
        slug = _SCHEMA_NAME_RE.sub("_", source).strip("_") or "metadata"
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
        return f"cam_{slug[:48]}_{digest}"[:64]
