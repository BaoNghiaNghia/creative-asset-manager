from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
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
    supports_batch = True

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
        batch_enabled: bool = False,
        batch_completion_window: str = "24h",
        batch_max_items: int = 1000,
        batch_max_file_bytes: int = 150_000_000,
        batch_poll_interval_seconds: float = 60.0,
        batch_result_chunk_size: int = 65_536,
        batch_input_retention_hours: int = 24,
        batch_output_retention_hours: int = 24,
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
        if batch_completion_window != "24h":
            raise ValueError("OpenAI batch completion window must be 24h")
        if min(batch_max_items, batch_max_file_bytes, batch_result_chunk_size,
               batch_input_retention_hours, batch_output_retention_hours) <= 0:
            raise ValueError("OpenAI batch limits and retention must be positive")
        if batch_max_items > 50_000:
            raise ValueError("OpenAI batch item limit cannot exceed 50000")
        if batch_max_file_bytes > 200_000_000:
            raise ValueError("OpenAI batch file limit cannot exceed 200 MB")
        if batch_poll_interval_seconds <= 0:
            raise ValueError("OpenAI batch poll interval must be positive")
        if batch_input_retention_hours > 720 or batch_output_retention_hours > 720:
            raise ValueError("OpenAI batch retention cannot exceed 720 hours")


        self.model = normalized_model
        self.default_model = normalized_model
        self.allowed_models = frozenset(normalized_allowed)
        self.timeout_seconds = float(timeout_seconds)
        self.image_detail = image_detail
        self.store_responses = bool(store_responses)
        self.capture_raw_response = bool(capture_raw_response)
        self.max_image_bytes = int(max_image_bytes)
        self.batch_enabled = bool(batch_enabled)
        self.batch_completion_window = batch_completion_window
        self.batch_max_items = int(batch_max_items)
        self.batch_max_request_bytes = int(batch_max_file_bytes)
        self.batch_poll_interval_seconds = float(batch_poll_interval_seconds)
        self.batch_result_chunk_size = int(batch_result_chunk_size)
        self.batch_input_retention_hours = int(batch_input_retention_hours)
        self.batch_output_retention_hours = int(batch_output_retention_hours)
        self._batch_files: dict[str, dict[str, str | None]] = {}
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
        self._ensure_batch_enabled()
        existing = await self._find_existing_batch(input.submission_key)
        if existing is not None:
            return self._submission(existing)
        path: str | None = None
        uploaded_file_id: str | None = None
        try:
            path, count, size = self._build_batch_file(input)
            if count != input.item_count:
                raise AiProviderError(
                    "OpenAI batch item count did not match the prepared input.",
                    code="openai_batch_item_count_mismatch", retryable=False)
            uploaded = await self._client.files.create(
                file=Path(path), purpose="batch",
                expires_after={"anchor": "created_at", "seconds":
                    self.batch_input_retention_hours * 3600},
                timeout=self.timeout_seconds)
            uploaded_file_id = str(self._value(uploaded, "id") or "")
            if not uploaded_file_id:
                raise AiProviderError(
                    "OpenAI did not return an input file identity.",
                    code="openai_batch_upload_invalid", retryable=True)
            try:
                batch = await self._client.batches.create(
                    input_file_id=uploaded_file_id,
                    endpoint="/v1/responses",
                    completion_window=self.batch_completion_window,
                    metadata={"cam_submission_key": input.submission_key,
                              "cam_tenant_id": input.tenant_id,
                              "cam_display_name": input.display_name},
                    output_expires_after={"anchor": "created_at", "seconds":
                        self.batch_output_retention_hours * 3600},
                    timeout=self.timeout_seconds)
            except Exception as exc:
                if isinstance(exc, openai.APIStatusError):
                    status_code = int(getattr(exc, "status_code", 0) or 0)
                    if status_code and status_code < 500:
                        raise self._batch_error(
                            exc, operation="submit") from exc
                recovered = await self._find_existing_batch(
                    input.submission_key, input_file_id=uploaded_file_id)
                if recovered is not None:
                    return self._submission(recovered)
                error = AiProviderError(
                    "OpenAI batch submission outcome is ambiguous.",
                    code="openai_batch_submission_ambiguous", retryable=True)
                error.provider_metadata = {"input_file_id": uploaded_file_id}
                error.__cause__ = exc
                raise error
            submission = self._submission(batch)
            self._batch_files[submission.provider_batch_id] = {
                "input_file_id": uploaded_file_id,
                "output_file_id": None, "error_file_id": None}
            return AiBatchSubmission(
                submission.provider_batch_id, submission.state,
                submission.provider_request_id,
                {"input_file_id": uploaded_file_id,
                 "transformed_item_count": count,
                 "transformed_file_bytes": size})
        except AiProviderError:
            raise
        except Exception as exc:
            raise self._batch_error(exc, operation="upload") from exc
        finally:
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

    async def get_batch_status(
        self, input: AiBatchStatusInput
    ) -> AiBatchStatus:
        self._ensure_batch_enabled()
        try:
            batch = await self._client.batches.retrieve(
                input.provider_batch_id, timeout=self.timeout_seconds)
        except Exception as exc:
            raise self._batch_error(exc, operation="status") from exc
        status = str(self._value(batch, "status") or "").lower()
        output_file_id = self._string_value(batch, "output_file_id")
        error_file_id = self._string_value(batch, "error_file_id")
        self._batch_files[input.provider_batch_id] = {
            "input_file_id": self._string_value(batch, "input_file_id"),
            "output_file_id": output_file_id,
            "error_file_id": error_file_id}
        state_map = {
            "validating": "pending", "in_progress": "running",
            "finalizing": "running", "cancelling": "running",
            "completed": "completed", "failed": "failed",
            "cancelled": "cancelled", "expired": "expired"}
        state = state_map.get(status)
        if state is None:
            raise AiProviderError(
                "OpenAI returned an unknown batch state.",
                code="openai_batch_unknown_state", retryable=True)
        if status == "expired" and (output_file_id or error_file_id):
            state = "completed"
        errors = self._object_dict(self._value(batch, "errors"))
        error_code, error_message = self._batch_status_error(errors, status)
        usage = self._usage(self._object_dict(self._value(batch, "usage")))
        request_counts = self._object_dict(self._value(batch, "request_counts"))
        if request_counts:
            usage["request_counts"] = request_counts
        return AiBatchStatus(
            state=state,
            retry_after_seconds=self.batch_poll_interval_seconds,
            usage=usage, error_code=error_code, error_message=error_message)

    async def stream_batch_results(
        self, input: AiBatchResultsInput
    ) -> AsyncIterator[AiBatchResult]:
        self._ensure_batch_enabled()
        files = self._batch_files.get(input.provider_batch_id)
        if files is None:
            await self.get_batch_status(AiBatchStatusInput(
                tenant_id=input.tenant_id,
                provider_batch_id=input.provider_batch_id))
            files = self._batch_files.get(input.provider_batch_id, {})
        start = int(input.cursor or "-1") + 1
        sequence = 0
        seen: set[str] = set()
        for kind, file_id in (("output", files.get("output_file_id")),
                              ("error", files.get("error_file_id"))):
            if not file_id:
                continue
            async for line in self._iter_file_lines(str(file_id)):
                if sequence < start:
                    sequence += 1
                    continue
                result = self._parse_batch_line(line, kind=kind)
                sequence += 1
                if result.custom_item_id in seen:
                    yield AiBatchResult(
                        custom_item_id=result.custom_item_id,
                        error_code="openai_batch_duplicate_result",
                        error_message="OpenAI returned a duplicate custom item ID.",
                        retryable=False,
                        provider_item_id=result.provider_item_id)
                    continue
                seen.add(result.custom_item_id)
                yield result

    async def cancel_batch(self, input: AiBatchStatusInput) -> bool:
        self._ensure_batch_enabled()
        try:
            current = await self._client.batches.retrieve(
                input.provider_batch_id, timeout=self.timeout_seconds)
            if str(self._value(current, "status") or "").lower() in {
                "completed", "failed", "expired", "cancelled"}:
                return True
            await self._client.batches.cancel(
                input.provider_batch_id, timeout=self.timeout_seconds)
            return True
        except Exception as exc:
            raise self._batch_error(exc, operation="cancel") from exc

    def _ensure_batch_enabled(self) -> None:
        if not self.batch_enabled:
            raise AiProviderError(
                "OpenAI batch analysis is disabled.",
                code="openai_batch_disabled", retryable=False)

    def _build_batch_file(
        self, input: AiBatchSubmissionInput
    ) -> tuple[str, int, int]:
        if input.model not in self.allowed_models:
            raise AiProviderError(
                "The OpenAI batch model is not allowed.",
                code="openai_model_not_allowed", retryable=False)
        if input.item_count <= 0 or input.item_count > self.batch_max_items:
            raise AiProviderError(
                "OpenAI batch item limit exceeded.",
                code="openai_batch_item_limit", retryable=False)
        source = Path(input.input_path)
        if not source.is_file():
            raise AiProviderError(
                "OpenAI batch input file is missing.",
                code="openai_batch_input_missing", retryable=False)
        handle = tempfile.NamedTemporaryFile(
            prefix="cam-openai-batch-", suffix=".jsonl", delete=False)
        path = handle.name
        os.chmod(path, 0o600)
        count = total = 0
        custom_ids: set[str] = set()
        try:
            with source.open("rb") as reader, handle:
                for raw_line in reader:
                    if len(raw_line) > self.batch_max_request_bytes:
                        raise AiProviderError(
                            "OpenAI batch input line is too large.",
                            code="openai_batch_line_too_large", retryable=False)
                    try:
                        neutral = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise AiProviderError(
                            "OpenAI batch input contains invalid JSON.",
                            code="openai_batch_invalid_jsonl",
                            retryable=False) from exc
                    if not isinstance(neutral, Mapping):
                        raise AiProviderError(
                            "OpenAI batch input row must be an object.",
                            code="openai_batch_invalid_row", retryable=False)
                    custom_id = neutral.get("custom_item_id")
                    if (not isinstance(custom_id, str) or not custom_id
                            or len(custom_id) > 128 or custom_id in custom_ids):
                        raise AiProviderError(
                            "OpenAI batch custom item ID is invalid or duplicated.",
                            code="openai_batch_invalid_custom_id",
                            retryable=False)
                    mime_type = neutral.get("image_mime_type")
                    encoded_image = neutral.get("image_base64")
                    if mime_type not in _SUPPORTED_IMAGE_TYPES or not isinstance(
                            encoded_image, str):
                        raise AiProviderError(
                            "OpenAI batch image is invalid.",
                            code="openai_unsupported_image", retryable=False)
                    try:
                        image = base64.b64decode(encoded_image, validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise AiProviderError(
                            "OpenAI batch image encoding is invalid.",
                            code="openai_batch_invalid_image",
                            retryable=False) from exc
                    if not image or len(image) > self.max_image_bytes:
                        raise AiProviderError(
                            "OpenAI batch image exceeds the input limit.",
                            code="openai_image_too_large", retryable=False)
                    prompt = neutral.get("prompt")
                    if not isinstance(prompt, str) or not prompt.strip():
                        raise AiProviderError(
                            "OpenAI batch prompt is invalid.",
                            code="openai_batch_invalid_prompt",
                            retryable=False)
                    schema = self._openai_schema(neutral.get("json_schema"))
                    if schema is None:
                        text_format = {"type": "json_object"}
                        prompt = prompt + (
                            "\n\nReturn exactly one JSON object and no "
                            "surrounding commentary.")
                    else:
                        text_format = {
                            "type": "json_schema",
                            "name": self._schema_name(
                                str(neutral.get("metadata_profile") or "metadata"),
                                str(neutral.get(
                                    "metadata_profile_version") or "1")),
                            "strict": True, "schema": schema}
                    request = {
                        "custom_id": custom_id, "method": "POST",
                        "url": "/v1/responses",
                        "body": {
                            "model": input.model,
                            "input": [{"role": "user", "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_image",
                                 "image_url": (
                                     f"data:{mime_type};base64,{encoded_image}"),
                                 "detail": self.image_detail}]}],
                            "text": {"format": text_format},
                            "store": self.store_responses}}
                    encoded = (json.dumps(
                        request, separators=(",", ":"), ensure_ascii=True)
                        + "\n").encode("utf-8")
                    total += len(encoded)
                    if total > self.batch_max_request_bytes:
                        raise AiProviderError(
                            "OpenAI batch file byte limit exceeded.",
                            code="openai_batch_file_too_large", retryable=False)
                    handle.write(encoded)
                    custom_ids.add(custom_id)
                    count += 1
                    if count > self.batch_max_items:
                        raise AiProviderError(
                            "OpenAI batch item limit exceeded.",
                            code="openai_batch_item_limit", retryable=False)
            return path, count, total
        except Exception:
            handle.close()
            Path(path).unlink(missing_ok=True)
            raise

    async def _find_existing_batch(
        self, submission_key: str, *, input_file_id: str | None = None
    ) -> Any | None:
        try:
            page = await self._client.batches.list(
                limit=100, timeout=self.timeout_seconds)
        except Exception:
            return None
        values = self._value(page, "data")
        if values is None and hasattr(page, "__aiter__"):
            values = [value async for value in page]
        for batch in values or ():
            metadata = self._object_dict(self._value(batch, "metadata"))
            if metadata.get("cam_submission_key") != submission_key:
                continue
            if input_file_id and self._string_value(
                    batch, "input_file_id") != input_file_id:
                continue
            return batch
        return None

    def _submission(self, batch: Any) -> AiBatchSubmission:
        batch_id = self._string_value(batch, "id")
        if not batch_id:
            raise AiProviderError(
                "OpenAI did not return a batch identity.",
                code="openai_batch_submission_invalid", retryable=True)
        return AiBatchSubmission(
            provider_batch_id=batch_id,
            state=str(self._value(batch, "status") or "submitted"),
            provider_request_id=self._string_value(batch, "_request_id"),
            provider_metadata={
                "input_file_id": self._string_value(batch, "input_file_id")})

    async def _iter_file_lines(self, file_id: str) -> AsyncIterator[str]:
        try:
            stream = self._client.files.with_streaming_response.content(
                file_id, timeout=self.timeout_seconds)
            async with stream as response:
                async for line in response.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode("utf-8")
                    if line:
                        yield str(line)
        except AiProviderError:
            raise
        except Exception as exc:
            raise self._batch_error(exc, operation="result_download") from exc

    def _parse_batch_line(self, line: str, *, kind: str) -> AiBatchResult:
        if len(line.encode("utf-8")) > self.batch_max_request_bytes:
            raise AiProviderError(
                "OpenAI batch result line is too large.",
                code="openai_batch_result_line_too_large", retryable=False)
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AiProviderError(
                "OpenAI batch result contains invalid JSON.",
                code="openai_batch_invalid_result", retryable=True) from exc
        if not isinstance(payload, Mapping):
            raise AiProviderError(
                "OpenAI batch result row must be an object.",
                code="openai_batch_invalid_result", retryable=True)
        custom_id = payload.get("custom_id")
        if not isinstance(custom_id, str) or not custom_id:
            raise AiProviderError(
                "OpenAI batch result has no custom item ID.",
                code="openai_batch_missing_custom_id", retryable=True)
        provider_item_id = (
            str(payload.get("id")) if payload.get("id") is not None else None)
        error = payload.get("error")
        response = payload.get("response")
        if kind == "error" or error:
            details = error if isinstance(error, Mapping) else {}
            code = str(details.get("code") or "openai_batch_item_failed")
            message = "OpenAI batch item failed."
            return AiBatchResult(
                custom_item_id=custom_id, error_code=self._stable_item_code(code),
                error_message=message[:500],
                retryable=self._item_error_retryable(code),
                provider_item_id=provider_item_id)
        if not isinstance(response, Mapping):
            return AiBatchResult(
                custom_item_id=custom_id,
                error_code="openai_batch_invalid_result",
                error_message="OpenAI batch response is missing.",
                retryable=True, provider_item_id=provider_item_id)
        status_code = int(response.get("status_code") or 0)
        body = response.get("body")
        if status_code < 200 or status_code >= 300 or not isinstance(body, Mapping):
            return AiBatchResult(
                custom_item_id=custom_id,
                error_code="openai_batch_item_failed",
                error_message="OpenAI batch item request failed.",
                retryable=status_code == 429 or status_code >= 500,
                provider_item_id=provider_item_id)
        text, refusal = self._response_text(body)
        status = str(body.get("status") or "").lower()
        if refusal:
            return AiBatchResult(
                custom_item_id=custom_id, error_code="openai_refusal",
                error_message="OpenAI refused the metadata request.",
                retryable=False, provider_item_id=provider_item_id)
        if status and status != "completed":
            return AiBatchResult(
                custom_item_id=custom_id,
                error_code="openai_incomplete_response",
                error_message="OpenAI returned an incomplete response.",
                retryable=True, provider_item_id=provider_item_id)
        try:
            metadata = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            metadata = None
        if not isinstance(metadata, Mapping):
            return AiBatchResult(
                custom_item_id=custom_id,
                error_code="openai_invalid_json",
                error_message="OpenAI returned invalid metadata JSON.",
                retryable=True, provider_item_id=provider_item_id)
        request_id = response.get("request_id") or body.get("id")
        result = AiMetadataAnalysisResult(
            metadata=dict(metadata), provider=self.provider_name,
            model=str(body.get("model") or self.model),
            provider_request_id=str(request_id) if request_id else None,
            usage=self._usage(self._object_dict(body.get("usage"))),
            provider_metadata={
                "response_id": body.get("id"), "status": status or None,
                "batch_item_id": provider_item_id},
            raw_response=dict(body) if self.capture_raw_response else None)
        return AiBatchResult(
            custom_item_id=custom_id, result=result,
            provider_item_id=provider_item_id)

    @staticmethod
    def _value(value: Any, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    @classmethod
    def _string_value(cls, value: Any, name: str) -> str | None:
        item = cls._value(value, name)
        return str(item) if item is not None else None

    @staticmethod
    def _batch_status_error(
        errors: Mapping[str, Any], status: str
    ) -> tuple[str | None, str | None]:
        data = errors.get("data")
        first = data[0] if isinstance(data, list) and data else None
        if isinstance(first, Mapping):
            code = str(first.get("code") or f"openai_batch_{status}")
            message = str(first.get("message") or "OpenAI batch failed.")
            return code[:100], message[:500]
        if status in {"failed", "expired", "cancelled"}:
            return f"openai_batch_{status}", f"OpenAI batch {status}."
        return None, None

    @staticmethod
    def _stable_item_code(code: str) -> str:
        normalized = re.sub(r"[^a-z0-9_]+", "_", code.lower()).strip("_")
        return ("openai_batch_" + (normalized or "item_failed"))[:100]

    @staticmethod
    def _item_error_retryable(code: str) -> bool:
        value = code.lower()
        return any(token in value for token in (
            "rate", "timeout", "server", "unavailable", "internal"))

    @classmethod
    def _batch_error(cls, exc: Exception, *, operation: str) -> AiProviderError:
        if isinstance(exc, openai.RateLimitError):
            return cls._error(
                "OpenAI batch rate limit was reached.",
                "openai_batch_rate_limit", True, exc)
        if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError,
                           asyncio.TimeoutError)):
            return cls._error(
                "OpenAI batch request could not be completed.",
                f"openai_batch_{operation}_temporary", True, exc)
        if isinstance(exc, openai.APIStatusError):
            status = int(getattr(exc, "status_code", 0) or 0)
            if status == 429 or status >= 500:
                return cls._error(
                    "OpenAI batch service is temporarily unavailable.",
                    "openai_batch_service_unavailable", True, exc)
            if status in {401, 403}:
                return cls._error(
                    "OpenAI batch authentication failed.",
                    "openai_authentication_failed", False, exc)
        return cls._error(
            "OpenAI batch request failed.",
            f"openai_batch_{operation}_failed", False, exc)
    async def aclose(self) -> None:
        closer = getattr(self._client, "close", None)
        if closer is None:
            return
        result = closer()
        if hasattr(result, "__await__"):
            await result


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
        for output in cls._value(response, "output") or ():
            for content in cls._value(output, "content") or ():
                content_type = cls._value(content, "type")
                if content_type == "refusal":
                    value = cls._value(content, "refusal")
                    refusal = str(value or "refused")
                elif content_type == "output_text":
                    value = cls._value(content, "text")
                    if isinstance(value, str):
                        texts.append(value)
        if not texts:
            value = cls._value(response, "output_text")
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
