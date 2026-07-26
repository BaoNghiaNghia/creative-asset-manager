from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo

import httpx

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


_PACIFIC_TIME = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class GeminiModelLimit:
    rpm: int
    tpm: int
    rpd: int


@dataclass
class _ModelRuntime:
    recent_requests: deque[float] = field(default_factory=deque)
    day: date | None = None
    daily_requests: int = 0
    cooldown_until: datetime | None = None
    daily_exhausted_until: datetime | None = None
    in_flight: bool = False


class GeminiAiMetadataProvider:
    """Gemini REST adapter. Domain code never depends on a Google AI SDK."""

    provider_name = "gemini"
    supports_single = True
    supports_batch = True
    batch_max_items = 100
    batch_max_request_bytes = 20_000_000

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.5-flash",
        timeout_seconds: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
        model_pool: tuple[str, ...] | None = None,
        model_limits: Mapping[str, GeminiModelLimit | tuple[int, int]] | None = None,
        cooldown_seconds: float = 60.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if not api_key:
            raise ValueError("Gemini API key is required")
        if not model:
            raise ValueError("Gemini model is required")
        if cooldown_seconds < 0:
            raise ValueError("Gemini cooldown must be non-negative")
        self._api_key = api_key
        self.model = model
        self.default_model = model
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10))
        self._transport = transport
        self._models = tuple(dict.fromkeys(model_pool or (model,)))
        self._limits = {
            name: self._coerce_model_limit(
                (model_limits or {}).get(name, GeminiModelLimit(1, 1, 1))
            )
            for name in self._models
        }
        if any(
            limit.rpm < 1 or limit.tpm < 1 or limit.rpd < 1
            for limit in self._limits.values()
        ):
            raise ValueError("Gemini model limits must be positive")
        self._runtime = {name: _ModelRuntime() for name in self._models}
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._sleeper = sleeper

    @staticmethod
    def _coerce_model_limit(value: GeminiModelLimit | tuple[int, int]) -> GeminiModelLimit:
        if isinstance(value, GeminiModelLimit):
            return value
        rpm, rpd = value
        return GeminiModelLimit(rpm=rpm, tpm=1, rpd=rpd)

    async def analyze_single(
        self, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult:
        self._check_cancelled(input)
        attempted_models: list[str] = []
        reasons: list[str] = []
        for model in self._models:
            unavailable = self._availability_reason(model)
            if unavailable is not None:
                reasons.append(f"{model}:{unavailable}")
                continue
            runtime = self._runtime[model]
            runtime.in_flight = True
            attempted_models.append(model)
            try:
                try:
                    result = await self._analyze_model(model, input)
                except AiProviderError as exc:
                    if exc.status_code == 429 and exc.details.get("daily_quota"):
                        self._mark_daily_exhausted(model)
                        reasons.append(f"{model}:daily_quota_exhausted")
                        continue
                    if exc.status_code == 429:
                        retry_after = exc.details.get("retry_after_seconds")
                        delay = 1.0 if retry_after is None else float(retry_after)
                        reasons.append(f"{model}:rpm_429_retry")
                        await self._sleeper(delay)
                        self._check_cancelled(input)
                        try:
                            result = await self._analyze_model(model, input)
                        except AiProviderError as retried:
                            if retried.status_code == 429 and retried.details.get("daily_quota"):
                                self._mark_daily_exhausted(model)
                                reasons.append(f"{model}:daily_quota_exhausted")
                                continue
                            if retried.status_code in {429, 503}:
                                self._mark_cooldown(model)
                                reasons.append(f"{model}:cooldown")
                                continue
                            raise self._with_failover_audit(retried, attempted_models, reasons)
                    elif exc.status_code == 503:
                        self._mark_cooldown(model)
                        reasons.append(f"{model}:cooldown")
                        continue
                    else:
                        raise self._with_failover_audit(exc, attempted_models, reasons)
                return self._with_failover_result(result, attempted_models, reasons)
            finally:
                runtime.in_flight = False
        raise AiProviderError(
            "No Gemini model is currently available.",
            code="gemini_model_pool_exhausted",
            retryable=True,
            details=self._audit_details(attempted_models, reasons),
        )

    async def _analyze_model(
        self, model: str, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult:
        self._record_request(model)
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": input.prompt},
                    {"inlineData": {
                        "mimeType": input.image_mime_type,
                        "data": base64.b64encode(input.image_bytes).decode("ascii"),
                    }},
                ],
            }],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    url,
                    headers={"x-goog-api-key": self._api_key},
                    json=body,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AiProviderError(
                "Gemini request could not be completed.",
                code="gemini_transport_error",
                retryable=True,
            ) from exc
        self._check_cancelled(input)
        self._raise_for_status(response)
        payload = self._response_object(response)
        text = self._candidate_text(payload)
        try:
            metadata = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AiProviderError(
                "Gemini returned malformed JSON.",
                code="gemini_invalid_json",
                retryable=True,
            ) from exc
        if not isinstance(metadata, Mapping):
            raise AiProviderError(
                "Gemini metadata root must be an object.",
                code="gemini_invalid_document",
                retryable=True,
            )
        candidate = (payload.get("candidates") or [{}])[0]
        return AiMetadataAnalysisResult(
            metadata=dict(metadata),
            provider=self.provider_name,
            model=payload.get("modelVersion") or model,
            provider_request_id=payload.get("responseId"),
            usage=dict(payload.get("usageMetadata") or {}),
            provider_metadata={
                "finish_reason": candidate.get("finishReason"),
                "model_version": payload.get("modelVersion"),
            },
            raw_response=payload,
        )

    def _availability_reason(self, model: str) -> str | None:
        runtime = self._runtime[model]
        now = datetime.now(timezone.utc)
        pacific_day = now.astimezone(_PACIFIC_TIME).date()
        if runtime.day != pacific_day:
            runtime.day, runtime.daily_requests = pacific_day, 0
            runtime.daily_exhausted_until = None
        if runtime.daily_exhausted_until and runtime.daily_exhausted_until > now:
            return "daily_quota_exhausted"
        if runtime.cooldown_until and runtime.cooldown_until > now:
            return "cooldown"
        if runtime.in_flight:
            return "concurrency_limited"
        limit = self._limits[model]
        rpm, rpd = limit.rpm, limit.rpd
        current = time.monotonic()
        while runtime.recent_requests and current - runtime.recent_requests[0] >= 60:
            runtime.recent_requests.popleft()
        if runtime.daily_requests >= rpd:
            self._mark_daily_exhausted(model)
            return "daily_quota_exhausted"
        if len(runtime.recent_requests) >= rpm:
            self._mark_cooldown(model)
            return "rpm_limit_reached"
        return None

    def _record_request(self, model: str) -> None:
        runtime = self._runtime[model]
        now = datetime.now(timezone.utc)
        pacific_day = now.astimezone(_PACIFIC_TIME).date()
        if runtime.day != pacific_day:
            runtime.day, runtime.daily_requests = pacific_day, 0
        runtime.daily_requests += 1
        runtime.recent_requests.append(time.monotonic())

    def _mark_cooldown(self, model: str) -> None:
        self._runtime[model].cooldown_until = datetime.now(timezone.utc) + self._cooldown

    def _mark_daily_exhausted(self, model: str) -> None:
        now = datetime.now(timezone.utc).astimezone(_PACIFIC_TIME)
        tomorrow = now.date() + timedelta(days=1)
        reset = datetime.combine(tomorrow, datetime.min.time(), tzinfo=_PACIFIC_TIME)
        self._runtime[model].daily_exhausted_until = reset.astimezone(timezone.utc)

    def _audit_details(self, attempted_models: list[str], reasons: list[str]) -> dict[str, Any]:
        return {
            "requested_model": self.model,
            "actual_model": None,
            "attempted_models": list(attempted_models),
            "failover_reason": ";".join(reasons) or None,
        }

    def _with_failover_result(
        self, result: AiMetadataAnalysisResult, attempted_models: list[str], reasons: list[str],
    ) -> AiMetadataAnalysisResult:
        metadata = {
            **dict(result.provider_metadata),
            **self._audit_details(attempted_models, reasons),
            "actual_model": result.model,
        }
        return AiMetadataAnalysisResult(
            metadata=result.metadata, provider=result.provider, model=result.model,
            provider_request_id=result.provider_request_id, usage=result.usage,
            provider_metadata=metadata, raw_response=result.raw_response,
        )

    def _with_failover_audit(
        self, exc: AiProviderError, attempted_models: list[str], reasons: list[str],
    ) -> AiProviderError:
        exc.details.update(self._audit_details(attempted_models, reasons))
        return exc

    async def submit_batch(self, input: AiBatchSubmissionInput) -> AiBatchSubmission:
        existing = await self._find_batch(input.display_name)
        if existing is not None:
            return AiBatchSubmission(
                provider_batch_id=str(existing["name"]),
                state=self._normalize_batch_state(existing.get("state")),
            )
        requests = []
        try:
            with open(input.input_path, "r", encoding="utf-8") as source:
                for line in source:
                    value = json.loads(line)
                    requests.append({
                        "request": {
                            "contents": [{"role": "user", "parts": [
                                {"text": value["prompt"]},
                                {"inlineData": {
                                    "mimeType": value["image_mime_type"],
                                    "data": value["image_base64"],
                                }},
                            ]}],
                            "generationConfig": {"responseMimeType": "application/json"},
                        },
                        "metadata": {"key": value["custom_item_id"]},
                    })
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AiProviderError(
                "Gemini batch input could not be read.",
                code="gemini_batch_invalid_input", retryable=False,
            ) from exc
        body = {"batch": {
            "displayName": input.display_name,
            "inputConfig": {"requests": {"requests": requests}},
        }}
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{input.model}:batchGenerateContent"
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    url, headers={"x-goog-api-key": self._api_key}, json=body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            recovered = await self._find_batch(input.display_name)
            if recovered is not None:
                return AiBatchSubmission(
                    provider_batch_id=str(recovered["name"]),
                    state=self._normalize_batch_state(recovered.get("state")),
                )
            raise AiProviderError(
                "Gemini batch submission outcome is ambiguous.",
                code="gemini_batch_submission_ambiguous", retryable=True,
            ) from exc
        self._raise_for_status(response)
        payload = self._response_object(response)
        name = payload.get("name") or (payload.get("batch") or {}).get("name")
        if not name:
            raise AiProviderError(
                "Gemini batch submission omitted its resource name.",
                code="gemini_batch_submission_ambiguous", retryable=True,
            )
        return AiBatchSubmission(
            provider_batch_id=str(name),
            state=self._normalize_batch_state(
                payload.get("state") or (payload.get("batch") or {}).get("state")),
            provider_request_id=payload.get("responseId"),
        )

    async def get_batch_status(self, input: AiBatchStatusInput) -> AiBatchStatus:
        operation = await self._get_batch(input.provider_batch_id)
        payload = self._batch_resource(operation)
        error = operation.get("error") or payload.get("error") or {}
        return AiBatchStatus(
            state=self._normalize_batch_state(payload.get("state")),
            retry_after_seconds=self._retry_after(operation),
            usage=dict(payload.get("usageMetadata") or operation.get("usageMetadata") or {}),
            error_code=str(error.get("code")) if error else None,
            error_message=str(error.get("message")) if error else None,
        )

    async def stream_batch_results(
        self, input: AiBatchResultsInput
    ) -> AsyncIterator[AiBatchResult]:
        operation = await self._get_batch(input.provider_batch_id)
        payload = self._batch_resource(operation)
        destination = payload.get("dest") or payload.get("destination") or payload.get("output") or {}
        responses = destination.get("inlinedResponses") or destination.get("inlined_responses") or []
        if isinstance(responses, Mapping):
            responses = (
                responses.get("inlinedResponses")
                or responses.get("inlined_responses")
                or []
            )
        start = int(input.cursor or "-1") + 1
        for index, value in enumerate(responses):
            if index < start:
                continue
            metadata = value.get("metadata") or {}
            custom_id = str(metadata.get("key") or value.get("key") or "")
            error = value.get("error")
            if error:
                code = str(error.get("status") or error.get("code") or "provider_item_failed")
                yield AiBatchResult(
                    custom_item_id=custom_id, error_code=code,
                    error_message=str(error.get("message") or "Gemini rejected batch item."),
                    retryable=code in {"429", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL"},
                )
                continue
            response = value.get("response") or value.get("inlineResponse") or {}
            try:
                text = self._candidate_text(response)
                document = json.loads(text)
                if not isinstance(document, Mapping):
                    raise TypeError("metadata root")
                candidate = (response.get("candidates") or [{}])[0]
                result = AiMetadataAnalysisResult(
                    metadata=dict(document), provider=self.provider_name,
                    model=response.get("modelVersion") or self.model,
                    provider_request_id=response.get("responseId"),
                    usage=dict(response.get("usageMetadata") or {}),
                    provider_metadata={
                        "finish_reason": candidate.get("finishReason"),
                        "model_version": response.get("modelVersion"),
                    },
                    raw_response=response,
                )
                yield AiBatchResult(custom_item_id=custom_id, result=result)
            except (AiProviderError, TypeError, json.JSONDecodeError) as exc:
                yield AiBatchResult(
                    custom_item_id=custom_id,
                    error_code="gemini_invalid_batch_result",
                    error_message=str(exc), retryable=True,
                )

    async def cancel_batch(self, input: AiBatchStatusInput) -> bool:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"{input.provider_batch_id}:cancel"
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    url, headers={"x-goog-api-key": self._api_key}, json={})
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AiProviderError(
                "Gemini batch cancellation could not be confirmed.",
                code="gemini_batch_cancel_transport", retryable=True,
            ) from exc
        self._raise_for_status(response)
        return True

    async def _get_batch(self, name: str) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/{name}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.get(url, headers={"x-goog-api-key": self._api_key})
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AiProviderError(
                "Gemini batch status could not be retrieved.",
                code="gemini_batch_transport_error", retryable=True,
            ) from exc
        self._raise_for_status(response)
        return self._response_object(response)

    async def _find_batch(self, display_name: str) -> dict[str, Any] | None:
        url = "https://generativelanguage.googleapis.com/v1beta/batches?pageSize=100"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.get(url, headers={"x-goog-api-key": self._api_key})
        except (httpx.TimeoutException, httpx.NetworkError):
            return None
        if response.status_code >= 400:
            return None
        payload = self._response_object(response)
        for operation in payload.get("batches") or payload.get("operations") or []:
            value = self._batch_resource(operation)
            if value.get("displayName") == display_name:
                if not value.get("name") and operation.get("name"):
                    value["name"] = operation["name"]
                return value
        return None

    @staticmethod
    def _normalize_batch_state(value: Any) -> str:
        state = str(value or "JOB_STATE_PENDING").upper()
        return {
            "JOB_STATE_PENDING": "pending",
            "JOB_STATE_RUNNING": "running",
            "JOB_STATE_SUCCEEDED": "completed",
            "JOB_STATE_FAILED": "failed",
            "JOB_STATE_CANCELLED": "cancelled",
            "JOB_STATE_EXPIRED": "expired",
            "BATCH_STATE_PENDING": "pending",
            "BATCH_STATE_RUNNING": "running",
            "BATCH_STATE_SUCCEEDED": "completed",
            "BATCH_STATE_FAILED": "failed",
            "BATCH_STATE_CANCELLED": "cancelled",
            "BATCH_STATE_EXPIRED": "expired",
        }.get(state, state.lower())

    @staticmethod
    def _batch_resource(operation: Mapping[str, Any]) -> dict[str, Any]:
        for key in ("response", "metadata"):
            value = operation.get(key)
            if isinstance(value, Mapping) and any(
                field in value for field in ("displayName", "state", "output")
            ):
                return dict(value)
        return dict(operation)

    @staticmethod
    def _retry_after(payload: Mapping[str, Any]) -> float | None:
        value = payload.get("retryAfterSeconds")
        try:
            return max(0.0, float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _response_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AiProviderError(
                "Gemini returned a non-JSON response.",
                code="gemini_invalid_response",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise AiProviderError(
                "Gemini returned an invalid response object.",
                code="gemini_invalid_response",
                retryable=True,
            )
        return payload

    @staticmethod
    def _candidate_text(payload: Mapping[str, Any]) -> str:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, Mapping)
            ).strip()
        except (KeyError, IndexError, TypeError):
            text = ""
        if not text:
            raise AiProviderError(
                "Gemini returned no metadata document.",
                code="gemini_empty_response",
                retryable=True,
            )
        return text

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        retry_after = None
        value = response.headers.get("retry-after")
        if value is not None:
            try:
                retry_after = max(0.0, float(value))
            except ValueError:
                retry_after = None
        body = response.text.lower()
        daily_quota = any(term in body for term in (
            "per day", "daily quota", "per-day", "rpd",
        ))
        retryable = (
            response.status_code in {408, 409, 425, 429}
            or response.status_code >= 500
        )
        raise AiProviderError(
            f"Gemini request failed with HTTP {response.status_code}.",
            code="gemini_rate_limited" if response.status_code == 429 else "gemini_http_error",
            retryable=retryable,
            status_code=response.status_code,
            details={
                "retry_after_seconds": retry_after,
                "daily_quota": daily_quota,
            },
        )

    @staticmethod
    def _check_cancelled(input: AiMetadataAnalysisInput) -> None:
        if input.is_cancelled is not None and input.is_cancelled():
            raise AiProviderError(
                "Gemini analysis was cancelled.",
                code="analysis_cancelled",
                retryable=True,
            )
