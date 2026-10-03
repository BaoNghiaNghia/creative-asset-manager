from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from app.core.config import Settings
from app.modules.ai_governance.metrics import AI_METRICS

_LOGGER = logging.getLogger("cam.providers.jev")
_BILLING_MARKERS = ("credit", "credits", "balance", "billing", "payment")
_RETRYABLE_HTTP_STATUSES = {500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class JevUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class JevCallResult:
    ok: bool
    source: str
    model: str | None = None
    answers: Mapping[str, Any] | None = None
    usage: JevUsage = JevUsage()
    latency_ms: int = 0
    estimated_cost_micros: int = 0
    fallback_reason: str | None = None
    http_status: int | None = None
    cached: bool = False


class JevClient:
    """Optional TypeSafe Jev client with fail-open fallback semantics."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.typesafe.ai",
        model: str = "jev-latest",
        timeout_seconds: float = 1.5,
        max_retries: int = 1,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: int = 300,
        billing_recheck_seconds: int = 3600,
        cache_ttl_seconds: int = 1800,
        daily_budget_usd: float = 1.0,
        monthly_budget_usd: float = 10.0,
        input_price_per_million_usd: float = 0.042,
        client: httpx.Client | None = None,
    ):
        self.api_key = str(api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.circuit_failure_threshold = circuit_failure_threshold
        self.circuit_open_seconds = circuit_open_seconds
        self.billing_recheck_seconds = billing_recheck_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.daily_budget_micros = max(0, round(daily_budget_usd * 1_000_000))
        self.monthly_budget_micros = max(0, round(monthly_budget_usd * 1_000_000))
        self.input_price_per_million_usd = input_price_per_million_usd
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        self._owns_client = client is None
        self._lock = threading.Lock()
        self._failure_streak = 0
        self._circuit_state = "closed"
        self._blocked_until_monotonic = 0.0
        self._cache: dict[str, tuple[float, JevCallResult]] = {}
        self._daily_key = ""
        self._monthly_key = ""
        self._daily_cost_micros = 0
        self._monthly_cost_micros = 0

    @property
    def circuit_state(self) -> str:
        with self._lock:
            return self._circuit_state

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def evaluate(
        self,
        *,
        state: str | Mapping[str, Any] | list[Any],
        questions: Mapping[str, Mapping[str, Any]],
    ) -> JevCallResult:
        now_monotonic = time.monotonic()
        if not self.api_key:
            return self._fallback("missing_api_key")
        try:
            cache_key = self._cache_key(state=state, questions=questions)
        except (TypeError, ValueError):
            return self._fallback("invalid_state")
        cached = self._cache_get(cache_key, now_monotonic)
        if cached is not None:
            AI_METRICS.increment(
                "jev_requests", provider="jev", mode="decision", outcome="cache_hit"
            )
            return cached

        blocked = self._blocked_result(now_monotonic)
        if blocked is not None:
            return blocked
        budget_reason = self._budget_reason()
        if budget_reason is not None:
            AI_METRICS.increment(
                "jev_requests",
                provider="jev",
                mode="decision",
                outcome=budget_reason,
            )
            return self._fallback(budget_reason)

        payload = {"state": state, "model": self.model, "questions": questions}
        attempts = self.max_retries + 1
        started = time.monotonic()
        for attempt in range(attempts):
            try:
                response = self._client.post(
                    f"{self.base_url}/v1/systemone",
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            except httpx.TimeoutException:
                if attempt + 1 < attempts:
                    continue
                return self._provider_failure(
                    "timeout", started=started, retryable=True
                )
            except httpx.HTTPError:
                if attempt + 1 < attempts:
                    continue
                return self._provider_failure(
                    "provider_unavailable", started=started, retryable=True
                )

            if response.status_code == 200:
                result = self._parse_success(response, started=started)
                if result.ok:
                    self._record_success(result)
                    self._cache_put(cache_key, result, now_monotonic)
                else:
                    self._record_failure(open_circuit=False)
                    AI_METRICS.increment(
                        "jev_requests",
                        provider="jev",
                        mode="decision",
                        outcome="invalid_response",
                    )
                    AI_METRICS.latency(
                        "jev",
                        "invalid_response",
                        result.latency_ms,
                        mode="decision",
                    )
                return result
            if self._is_billing_block(response):
                return self._billing_failure(response.status_code, started=started)
            if response.status_code == 429:
                return self._provider_failure(
                    "rate_limit",
                    started=started,
                    http_status=429,
                    retryable=True,
                )
            if response.status_code in _RETRYABLE_HTTP_STATUSES:
                if attempt + 1 < attempts:
                    continue
                return self._provider_failure(
                    "provider_unavailable",
                    started=started,
                    http_status=response.status_code,
                    retryable=True,
                )
            return self._provider_failure(
                "invalid_response",
                started=started,
                http_status=response.status_code,
                retryable=False,
            )

        return self._provider_failure(
            "provider_unavailable", started=started, retryable=True
        )

    def _parse_success(
        self, response: httpx.Response, *, started: float
    ) -> JevCallResult:
        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        try:
            body = response.json()
            model = str(body["model"])
            answers = body["answers"]
            usage = body["usage"]
            input_tokens = int(usage["input_tokens"])
            output_tokens = int(usage["output_tokens"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._fallback(
                "invalid_response", latency_ms=latency_ms, http_status=200
            )
        if not isinstance(answers, dict) or input_tokens < 0 or output_tokens < 0:
            return self._fallback(
                "invalid_response", latency_ms=latency_ms, http_status=200
            )
        cost_micros = round(input_tokens * self.input_price_per_million_usd)
        return JevCallResult(
            ok=True,
            source="provider",
            model=model,
            answers=answers,
            usage=JevUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            latency_ms=latency_ms,
            estimated_cost_micros=cost_micros,
            http_status=200,
        )

    def _record_success(self, result: JevCallResult) -> None:
        with self._lock:
            self._failure_streak = 0
            self._circuit_state = "closed"
            self._blocked_until_monotonic = 0.0
            self._roll_budget_windows_locked()
            self._daily_cost_micros += result.estimated_cost_micros
            self._monthly_cost_micros += result.estimated_cost_micros
        AI_METRICS.increment(
            "jev_requests", provider="jev", mode="decision", outcome="completed"
        )
        AI_METRICS.increment(
            "jev_input_tokens",
            provider="jev",
            mode="decision",
            outcome="completed",
            value=result.usage.input_tokens,
        )
        AI_METRICS.increment(
            "jev_estimated_cost_micros",
            provider="jev",
            mode="decision",
            outcome="completed",
            value=result.estimated_cost_micros,
        )
        AI_METRICS.latency(
            "jev", "completed", result.latency_ms, mode="decision"
        )

    def _record_failure(self, *, open_circuit: bool) -> None:
        with self._lock:
            self._failure_streak += 1
            if open_circuit or self._failure_streak >= self.circuit_failure_threshold:
                self._circuit_state = "open"
                self._blocked_until_monotonic = (
                    time.monotonic() + self.circuit_open_seconds
                )

    def _provider_failure(
        self,
        reason: str,
        *,
        started: float,
        http_status: int | None = None,
        retryable: bool,
    ) -> JevCallResult:
        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        self._record_failure(open_circuit=False)
        AI_METRICS.increment(
            "jev_requests", provider="jev", mode="decision", outcome=reason
        )
        AI_METRICS.latency("jev", reason, latency_ms, mode="decision")
        _LOGGER.warning(
            "jev_fallback",
            extra={
                "reason": reason,
                "http_status": http_status,
                "retryable": retryable,
                "circuit_state": self.circuit_state,
            },
        )
        return self._fallback(
            reason, latency_ms=latency_ms, http_status=http_status
        )

    def _billing_failure(
        self, http_status: int, *, started: float
    ) -> JevCallResult:
        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        with self._lock:
            self._failure_streak += 1
            self._circuit_state = "billing_blocked"
            self._blocked_until_monotonic = (
                time.monotonic() + self.billing_recheck_seconds
            )
        AI_METRICS.increment(
            "jev_requests",
            provider="jev",
            mode="decision",
            outcome="billing_blocked",
        )
        AI_METRICS.latency(
            "jev", "billing_blocked", latency_ms, mode="decision"
        )
        _LOGGER.warning(
            "jev_billing_blocked", extra={"http_status": http_status}
        )
        return self._fallback(
            "billing_blocked",
            latency_ms=latency_ms,
            http_status=http_status,
        )

    def _blocked_result(self, now_monotonic: float) -> JevCallResult | None:
        with self._lock:
            state = self._circuit_state
            blocked_until = self._blocked_until_monotonic
            if state in {"open", "billing_blocked"} and now_monotonic < blocked_until:
                reason = (
                    "billing_blocked"
                    if state == "billing_blocked"
                    else "circuit_open"
                )
            elif state in {"open", "billing_blocked"}:
                self._circuit_state = "half_open"
                self._blocked_until_monotonic = 0.0
                return None
            elif state == "half_open":
                reason = "circuit_open"
            else:
                return None
        AI_METRICS.increment(
            "jev_requests", provider="jev", mode="decision", outcome=reason
        )
        return self._fallback(reason)

    def _budget_reason(self) -> str | None:
        with self._lock:
            self._roll_budget_windows_locked()
            if (
                self.daily_budget_micros > 0
                and self._daily_cost_micros >= self.daily_budget_micros
            ):
                return "daily_budget_exceeded"
            if (
                self.monthly_budget_micros > 0
                and self._monthly_cost_micros >= self.monthly_budget_micros
            ):
                return "monthly_budget_exceeded"
        return None

    def _roll_budget_windows_locked(self) -> None:
        now = datetime.now(timezone.utc)
        daily_key = now.date().isoformat()
        monthly_key = f"{now.year:04d}-{now.month:02d}"
        if self._daily_key != daily_key:
            self._daily_key = daily_key
            self._daily_cost_micros = 0
        if self._monthly_key != monthly_key:
            self._monthly_key = monthly_key
            self._monthly_cost_micros = 0

    def _cache_key(
        self,
        *,
        state: str | Mapping[str, Any] | list[Any],
        questions: Mapping[str, Mapping[str, Any]],
    ) -> str:
        payload = json.dumps(
            {"model": self.model, "state": state, "questions": questions},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _cache_get(
        self, cache_key: str, now_monotonic: float
    ) -> JevCallResult | None:
        if self.cache_ttl_seconds <= 0:
            return None
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is None:
                return None
            expires_at, result = cached
            if now_monotonic >= expires_at:
                self._cache.pop(cache_key, None)
                return None
        return replace(result, source="cache", cached=True, latency_ms=0)

    def _cache_put(
        self,
        cache_key: str,
        result: JevCallResult,
        now_monotonic: float,
    ) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        with self._lock:
            self._cache[cache_key] = (
                now_monotonic + self.cache_ttl_seconds,
                result,
            )
            if len(self._cache) > 2048:
                oldest = min(self._cache, key=lambda key: self._cache[key][0])
                self._cache.pop(oldest, None)

    @staticmethod
    def _is_billing_block(response: httpx.Response) -> bool:
        if response.status_code == 402:
            return True
        if response.status_code not in {400, 403}:
            return False
        safe_text = response.text[:1000].casefold()
        return any(marker in safe_text for marker in _BILLING_MARKERS)

    @staticmethod
    def _fallback(
        reason: str,
        *,
        latency_ms: int = 0,
        http_status: int | None = None,
    ) -> JevCallResult:
        return JevCallResult(
            ok=False,
            source="fallback",
            fallback_reason=reason,
            latency_ms=latency_ms,
            http_status=http_status,
        )


def build_jev_client(settings: Settings) -> JevClient | None:
    """Build Jev only when explicitly enabled and configured."""

    if not settings.JEV_ENABLED:
        return None
    api_key = settings.JEV_API_KEY.get_secret_value().strip()
    if not api_key:
        _LOGGER.warning("jev_enabled_without_api_key")
        return None
    return JevClient(
        api_key,
        base_url=settings.JEV_BASE_URL,
        model=settings.JEV_MODEL,
        timeout_seconds=settings.JEV_TIMEOUT_SECONDS,
        max_retries=settings.JEV_MAX_RETRIES,
        circuit_failure_threshold=settings.JEV_CIRCUIT_FAILURE_THRESHOLD,
        circuit_open_seconds=settings.JEV_CIRCUIT_OPEN_SECONDS,
        billing_recheck_seconds=settings.JEV_BILLING_RECHECK_SECONDS,
        cache_ttl_seconds=settings.JEV_CACHE_TTL_SECONDS,
        daily_budget_usd=settings.JEV_DAILY_BUDGET_USD,
        monthly_budget_usd=settings.JEV_MONTHLY_BUDGET_USD,
        input_price_per_million_usd=settings.JEV_INPUT_PRICE_PER_MILLION_USD,
    )
