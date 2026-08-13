from __future__ import annotations

import asyncio
import base64
import json
import math
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from collections.abc import AsyncIterator
from urllib.parse import urlsplit
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
_MAX_ERROR_DETAIL_CHARS = 1_000
_LOGGER = logging.getLogger("cam.providers.gemini")


def validate_gemini_api_key(api_key: str, *, timeout_seconds: float = 10.0) -> str:
    """Return a safe normalized status for a server-side Gemini key probe.

    The models list endpoint is substantially cheaper than an analysis request.
    Callers deliberately receive no provider body or credential material.
    """
    try:
        response = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": api_key},
            timeout=timeout_seconds,
        )
    except httpx.HTTPError:
        return "PROVIDER_UNAVAILABLE"
    if response.status_code == 200:
        return "VALID"
    if response.status_code in (400, 401):
        return "INVALID_KEY"
    if response.status_code == 403:
        return "PERMISSION_DENIED"
    if response.status_code == 429:
        return "RATE_LIMITED"
    return "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True)
class GeminiModelLimit:
    rpm: int
    tpm: int
    rpd: int


class GeminiProjectQuotaCoordinator(Protocol):
    def reserve_request(
        self, *, model: str, rpd: int, now: datetime
    ) -> "GeminiModelUnavailable | None": ...

    def block_until(self, *, model: str, retry_at: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class GeminiModelUnavailable:
    model: str
    reason: str
    available_at: datetime
    permanent: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "reason": self.reason,
            "available_at": self.available_at.isoformat(),
            "permanent": self.permanent,
        }


class GeminiPoolTemporarilyUnavailable(AiProviderError):
    def __init__(
        self,
        *,
        attempted_models: list[str],
        reasons_by_model: Mapping[str, GeminiModelUnavailable],
        earliest_retry_at: datetime,
    ):
        if earliest_retry_at.tzinfo is None or earliest_retry_at.utcoffset() is None:
            raise ValueError("earliest_retry_at must be timezone-aware")
        self.provider = "gemini"
        self.attempted_models = tuple(attempted_models)
        self.reasons_by_model = dict(reasons_by_model)
        self.earliest_retry_at = earliest_retry_at
        super().__init__(
            "No Gemini model is currently available.",
            code="gemini_model_pool_temporarily_unavailable",
            retryable=True,
            details={
                "provider": "gemini",
                "attempted_models": list(self.attempted_models),
                "reasons_by_model": {
                    model: unavailable.as_dict()
                    for model, unavailable in self.reasons_by_model.items()
                },
                "earliest_retry_at": earliest_retry_at.isoformat(),
            },
        )


@dataclass
class _InputTokenReservation:
    recorded_at: float
    tokens: int


@dataclass
class _ModelRuntime:
    recent_requests: deque[float] = field(default_factory=deque)
    recent_input_tokens: deque[_InputTokenReservation] = field(default_factory=deque)
    day: date | None = None
    daily_requests: int = 0
    cooldown_until: datetime | None = None
    daily_exhausted_until: datetime | None = None
    in_flight: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
        model_limits: Mapping[str, GeminiModelLimit] | None = None,
        cooldown_seconds: float = 60.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        quota_coordinator: GeminiProjectQuotaCoordinator | None = None,
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
        if not isinstance(model_limits, Mapping):
            raise ValueError(
                "Gemini model limits are required for model pool: "
                + ", ".join(self._models)
            )
        missing_models = [name for name in self._models if name not in model_limits]
        if missing_models:
            raise ValueError(
                "Gemini model limits are missing required model(s): "
                + ", ".join(missing_models)
            )
        invalid_models = [
            name
            for name in self._models
            if not isinstance(model_limits[name], GeminiModelLimit)
        ]
        if invalid_models:
            raise ValueError(
                "Gemini model limits must use GeminiModelLimit values for: "
                + ", ".join(invalid_models)
            )
        self._limits: dict[str, GeminiModelLimit] = {}
        for name in self._models:
            limit = model_limits[name]
            if limit.rpm < 1 or limit.tpm < 1 or limit.rpd < 1:
                raise ValueError("Gemini model limits must be positive")
            self._limits[name] = limit
        self._runtime = {name: _ModelRuntime() for name in self._models}
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._sleeper = sleeper
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._quota_coordinator = quota_coordinator


    async def analyze_single(
        self, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult:
        self._check_cancelled(input)
        attempted_models: list[str] = []
        reasons: list[str] = []
        unavailable_by_model: dict[str, GeminiModelUnavailable] = {}
        models = self._models
        if input.preferred_model in self._models:
            models = (input.preferred_model,) + tuple(
                model for model in self._models if model != input.preferred_model
            )
        for model in models:
            reservation, unavailable = await self._reserve_request(model, input)
            if unavailable is not None:
                unavailable_by_model[model] = unavailable
                reasons.append(f"{model}:{unavailable.reason}")
                continue
            assert reservation is not None
            attempted_models.append(model)
            try:
                try:
                    result = await self._analyze_model(model, input)
                except AiProviderError as exc:
                    if exc.status_code == 429:
                        unavailable = await self._mark_quota_unavailable(
                            model,
                            str(exc.details.get("quota_reason") or "rpm_exhausted"),
                            exc.details.get("retry_after_seconds"),
                        )
                        unavailable_by_model[model] = unavailable
                        reasons.append(f"{model}:{unavailable.reason}")
                        continue
                    if exc.status_code == 503 or exc.code == "gemini_model_or_method_not_found":
                        unavailable = await self._mark_cooldown(
                            model, exc.details.get("retry_after_seconds")
                        )
                        unavailable_by_model[model] = unavailable
                        reasons.append(f"{model}:{unavailable.reason}")
                        continue
                    raise self._with_failover_audit(exc, attempted_models, reasons)
                await self._reconcile_input_tokens(model, reservation, result.usage)
                return self._with_failover_result(result, attempted_models, reasons)
            finally:
                await self._release_request(model)

        if unavailable_by_model:
            earliest_retry_at = min(
                unavailable.available_at
                for unavailable in unavailable_by_model.values()
            )
            raise GeminiPoolTemporarilyUnavailable(
                attempted_models=attempted_models,
                reasons_by_model=unavailable_by_model,
                earliest_retry_at=earliest_retry_at,
            )
        raise AiProviderError(
            "No Gemini model is currently available.",
            code="gemini_model_pool_exhausted",
            retryable=True,
            details=self._audit_details(attempted_models, reasons),
        )

    async def _analyze_model(
        self, model: str, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult:
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
        self._raise_for_status(response, model=model, input=input)
        payload = self._response_object(response)
        text = self._candidate_text(payload)
        try:
            metadata = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AiProviderError(
                "Gemini returned malformed JSON.",
                code="gemini_invalid_json",
                retryable=False,
            ) from exc
        if not isinstance(metadata, Mapping):
            raise AiProviderError(
                "Gemini metadata root must be an object.",
                code="gemini_invalid_document",
                retryable=False,
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

    async def _reserve_request(
        self, model: str, input: AiMetadataAnalysisInput
    ) -> tuple[_InputTokenReservation | None, GeminiModelUnavailable | None]:
        runtime = self._runtime[model]
        async with runtime.lock:
            now = self._aware_now()
            pacific_day = now.astimezone(_PACIFIC_TIME).date()
            if runtime.day != pacific_day:
                runtime.day, runtime.daily_requests = pacific_day, 0
                runtime.daily_exhausted_until = None
            if runtime.daily_exhausted_until and runtime.daily_exhausted_until > now:
                return None, self._unavailable(
                    model, "rpd_exhausted", runtime.daily_exhausted_until, now
                )
            if runtime.cooldown_until and runtime.cooldown_until > now:
                return None, self._unavailable(
                    model, "cooldown", runtime.cooldown_until, now
                )
            if runtime.in_flight:
                return None, self._unavailable(
                    model, "in_flight", now + timedelta(seconds=1), now
                )

            current = self._clock()
            self._prune_rolling_window(runtime, current)
            limit = self._limits[model]
            if runtime.daily_requests >= limit.rpd:
                self._set_daily_exhausted(runtime)
                return None, self._unavailable(
                    model, "rpd_exhausted", runtime.daily_exhausted_until, now
                )
            if len(runtime.recent_requests) >= limit.rpm:
                return None, self._unavailable(
                    model,
                    "rpm_exhausted",
                    self._rolling_available_at(
                        now, current, runtime.recent_requests[0]
                    ),
                    now,
                )

            estimated_tokens = self._estimate_input_tokens(input)
            reserved_tokens = sum(item.tokens for item in runtime.recent_input_tokens)
            if reserved_tokens + estimated_tokens > limit.tpm:
                return None, self._unavailable(
                    model,
                    "tpm_exhausted",
                    self._tpm_available_at(
                        now, current, runtime.recent_input_tokens,
                        reserved_tokens, estimated_tokens, limit.tpm,
                    ),
                    now,
                )

            if self._quota_coordinator is not None:
                shared_unavailable = self._quota_coordinator.reserve_request(
                    model=model, rpd=limit.rpd, now=now
                )
                if shared_unavailable is not None:
                    return None, shared_unavailable

            reservation = _InputTokenReservation(
                recorded_at=current,
                tokens=estimated_tokens,
            )
            runtime.recent_requests.append(current)
            runtime.recent_input_tokens.append(reservation)
            runtime.daily_requests += 1
            runtime.in_flight = True
            return reservation, None

    async def _release_request(self, model: str) -> None:
        runtime = self._runtime[model]
        async with runtime.lock:
            runtime.in_flight = False

    async def _reconcile_input_tokens(
        self,
        model: str,
        reservation: _InputTokenReservation,
        usage: Mapping[str, Any],
    ) -> None:
        actual_tokens = self._actual_input_tokens(usage)
        if actual_tokens is None:
            return
        runtime = self._runtime[model]
        async with runtime.lock:
            if any(item is reservation for item in runtime.recent_input_tokens):
                reservation.tokens = actual_tokens

    async def _mark_cooldown(
        self, model: str, retry_after_seconds: Any | None = None
    ) -> GeminiModelUnavailable:
        seconds = self._cooldown.total_seconds()
        if isinstance(retry_after_seconds, (int, float)) and retry_after_seconds >= 0:
            seconds = float(retry_after_seconds)
        runtime = self._runtime[model]
        async with runtime.lock:
            now = self._aware_now()
            cooldown_until = now + timedelta(seconds=seconds)
            if runtime.cooldown_until is None or cooldown_until > runtime.cooldown_until:
                runtime.cooldown_until = cooldown_until
            return self._unavailable(model, "cooldown", runtime.cooldown_until, now)

    async def _mark_quota_unavailable(
        self, model: str, reason: str, retry_after_seconds: Any | None
    ) -> GeminiModelUnavailable:
        if reason == "rpd_exhausted":
            await self._mark_daily_exhausted(model, retry_after_seconds)
            runtime = self._runtime[model]
            async with runtime.lock:
                now = self._aware_now()
                retry_at = runtime.daily_exhausted_until or now + timedelta(seconds=1)
                if self._quota_coordinator is not None:
                    self._quota_coordinator.block_until(model=model, retry_at=retry_at)
                _LOGGER.warning(
                    "gemini_model_daily_quota_deferred",
                    extra={
                        "model": model,
                        "reason": reason,
                        "retry_at": retry_at.isoformat(),
                        "provider_call_started": False,
                    },
                )
                return self._unavailable(
                    model, reason, runtime.daily_exhausted_until, now
                )
        cooldown = await self._mark_cooldown(model, retry_after_seconds)
        return GeminiModelUnavailable(
            model=model,
            reason=reason if reason in {"rpm_exhausted", "tpm_exhausted"} else cooldown.reason,
            available_at=cooldown.available_at,
        )

    async def _mark_daily_exhausted(
        self, model: str, retry_after_seconds: Any | None = None
    ) -> None:
        runtime = self._runtime[model]
        async with runtime.lock:
            self._set_daily_exhausted(runtime, retry_after_seconds)

    def _set_daily_exhausted(
        self, runtime: _ModelRuntime, retry_after_seconds: Any | None = None
    ) -> None:
        now = self._aware_now().astimezone(_PACIFIC_TIME)
        tomorrow = now.date() + timedelta(days=1)
        reset = datetime.combine(tomorrow, datetime.min.time(), tzinfo=_PACIFIC_TIME)
        retry_at = reset.astimezone(timezone.utc)
        if isinstance(retry_after_seconds, (int, float)) and retry_after_seconds >= 0:
            retry_at = max(
                retry_at,
                self._aware_now() + timedelta(seconds=float(retry_after_seconds)),
            )
        runtime.daily_exhausted_until = retry_at

    def _aware_now(self) -> datetime:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    @staticmethod
    def _unavailable(
        model: str, reason: str, available_at: datetime | None, now: datetime
    ) -> GeminiModelUnavailable:
        candidate = available_at or now + timedelta(seconds=1)
        if candidate.tzinfo is None or candidate.utcoffset() is None:
            candidate = candidate.replace(tzinfo=timezone.utc)
        candidate = candidate.astimezone(timezone.utc)
        if candidate <= now:
            candidate = now + timedelta(milliseconds=1)
        return GeminiModelUnavailable(model=model, reason=reason, available_at=candidate)

    @staticmethod
    def _rolling_available_at(
        now: datetime, current: float, recorded_at: float
    ) -> datetime:
        return now + timedelta(seconds=max(0.0, 60 - (current - recorded_at)))

    @classmethod
    def _tpm_available_at(
        cls,
        now: datetime,
        current: float,
        reservations: deque[_InputTokenReservation],
        reserved_tokens: int,
        estimated_tokens: int,
        limit: int,
    ) -> datetime:
        remaining = reserved_tokens
        for reservation in reservations:
            remaining -= reservation.tokens
            if remaining + estimated_tokens <= limit:
                return cls._rolling_available_at(now, current, reservation.recorded_at)
        return now + timedelta(seconds=60)

    @staticmethod
    def _estimate_input_tokens(input: AiMetadataAnalysisInput) -> int:
        prompt_tokens = math.ceil(len(input.prompt.encode("utf-8")) / 4)
        width = input.image_width or 384
        height = input.image_height or 384
        if width <= 0 or height <= 0:
            width = height = 384
        tiles_x = math.ceil(width / 768)
        tiles_y = math.ceil(height / 768)
        image_tokens = 258 * tiles_x * tiles_y
        return max(1, prompt_tokens + image_tokens)

    @staticmethod
    def _actual_input_tokens(usage: Mapping[str, Any]) -> int | None:
        for key in ("promptTokenCount", "inputTokenCount"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                return value
        return None

    @staticmethod
    def _prune_rolling_window(runtime: _ModelRuntime, current: float) -> None:
        while runtime.recent_requests and current - runtime.recent_requests[0] >= 60:
            runtime.recent_requests.popleft()
        while (
            runtime.recent_input_tokens
            and current - runtime.recent_input_tokens[0].recorded_at >= 60
        ):
            runtime.recent_input_tokens.popleft()

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
    def _retry_after(payload: Mapping[str, Any] | httpx.Response) -> float | None:
        if isinstance(payload, httpx.Response):
            header = payload.headers.get("retry-after")
            try:
                return max(0.0, float(header)) if header is not None else None
            except ValueError:
                return None
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
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise AiProviderError(
                "Gemini returned an invalid response object.",
                code="gemini_invalid_response",
                retryable=False,
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
                retryable=False,
            )
        return text

    def _raise_for_status(
        self,
        response: httpx.Response,
        *,
        model: str | None = None,
        input: AiMetadataAnalysisInput | None = None,
    ) -> None:
        if response.status_code < 400:
            return
        retry_after = self._retry_after(response)
        quota_reason = self._quota_reason(response)
        details = self._error_details(response, model=model, input=input)
        details.update(
            {
                "retry_after_seconds": retry_after,
                "daily_quota": quota_reason == "rpd_exhausted",
                "quota_reason": quota_reason,
            }
        )
        status = response.status_code
        code = "gemini_rate_limited" if status == 429 else "gemini_http_error"
        retryable = status in {408, 409, 425, 429} or status >= 500
        message = f"Gemini request failed with HTTP {status}."
        if status == 404:
            classification = self._classify_not_found(details)
            if classification == "model_or_method":
                code = "gemini_model_or_method_not_found"
                retryable = True
                message = "The configured Gemini model or generateContent method was not found."
            elif classification == "resource_or_input":
                code = "gemini_input_resource_not_found"
                retryable = False
                message = "Gemini could not find a referenced input resource."
            else:
                code = "gemini_http_not_found"
                retryable = False
                message = "Gemini request target was not found."
        _LOGGER.warning(
            "gemini_http_error",
            extra={
                "actual_model": model,
                "endpoint_path": details.get("endpoint_path"),
                "http_status": status,
                "google_error_status": details.get("google_error_status"),
                "google_error_message": details.get("google_error_message"),
                "provider_request_id": details.get("provider_request_id"),
                "analysis_id": input.analysis_id if input else None,
                "pipeline_id": input.pipeline_id if input else None,
                "error_code": code,
            },
        )
        raise AiProviderError(
            message,
            code=code,
            retryable=retryable,
            status_code=status,
            details=details,
        )

    def _error_details(
        self,
        response: httpx.Response,
        *,
        model: str | None,
        input: AiMetadataAnalysisInput | None,
    ) -> dict[str, Any]:
        payload: Mapping[str, Any] = {}
        try:
            value = response.json()
            if isinstance(value, Mapping):
                payload = value
        except ValueError:
            pass
        error = payload.get("error")
        error = error if isinstance(error, Mapping) else {}
        request_id = next(
            (
                response.headers.get(header)
                for header in ("x-goog-request-id", "x-request-id", "x-cloud-trace-context")
                if response.headers.get(header)
            ),
            None,
        )
        try:
            endpoint_path = urlsplit(str(response.url)).path
        except Exception:
            endpoint_path = "/v1beta/models"
        excerpt = (
            json.dumps({"error": dict(error)}, ensure_ascii=False, sort_keys=True)
            if error
            else response.text
        )
        return {
            "actual_model": model,
            "endpoint_path": self._sanitize_error_text(endpoint_path),
            "http_status": response.status_code,
            "google_error_status": self._sanitize_error_text(error.get("status")),
            "google_error_message": self._sanitize_error_text(error.get("message")),
            "provider_request_id": self._sanitize_error_text(request_id),
            "provider_response_excerpt": self._sanitize_error_text(excerpt),
            "analysis_id": input.analysis_id if input else None,
            "pipeline_id": input.pipeline_id if input else None,
        }

    def _sanitize_error_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).replace(self._api_key, "[REDACTED]")
        text = re.sub(
            r'(?i)("?(?:api[_-]?key|authorization|key)"?\s*[:=]\s*"?)[^\s,;"]+',
            r"[REDACTED]",
            text,
        )
        return text[:_MAX_ERROR_DETAIL_CHARS]

    @staticmethod
    def _classify_not_found(details: Mapping[str, Any]) -> str:
        text = " ".join(
            str(details.get(key) or "").lower()
            for key in (
                "google_error_status",
                "google_error_message",
                "provider_response_excerpt",
            )
        )
        if any(marker in text for marker in ("model", "generatecontent", "method", "api version")):
            return "model_or_method"
        if any(marker in text for marker in ("resource", "input", "file", "image", "contents")):
            return "resource_or_input"
        return "unknown"

    @staticmethod
    def _quota_reason(response: httpx.Response) -> str:
        values = [response.text.lower()]
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping):
                details = error.get("details")
                if isinstance(details, list):
                    values.extend(str(detail).lower() for detail in details)
                for key in ("message", "status", "quotaMetric", "quotaId"):
                    value = error.get(key)
                    if value is not None:
                        values.append(str(value).lower())
        text = " ".join(values)
        normalized = re.sub(r"[^a-z0-9]+", "", text)
        if any(term in text for term in ("per day", "daily quota", "per-day", "rpd")) or any(
            term in normalized
            for term in (
                "requestsperday",
                "requestperday",
                "generaterequestsperday",
                "requestsperprojectpermodel",
            )
        ):
            return "rpd_exhausted"
        if any(term in text for term in (
            "tokens per minute", "token count per minute", "input_token", "tpm",
        )) or any(term in normalized for term in ("tokensperminute", "tokenperminute")):
            return "tpm_exhausted"
        return "rpm_exhausted"

    @staticmethod
    def _check_cancelled(input: AiMetadataAnalysisInput) -> None:
        if input.is_cancelled is not None and input.is_cancelled():
            raise AiProviderError(
                "Gemini analysis was cancelled.",
                code="analysis_cancelled",
                retryable=True,
            )
