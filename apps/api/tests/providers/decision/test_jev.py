from __future__ import annotations

import httpx

from app.core.config import Settings
from app.providers.decision.jev import JevClient, build_jev_client


def _questions() -> dict:
    return {
        "next_context": {
            "type": "choice",
            "instructions": "Choose the next Scout search context.",
            "criteria": {
                "same_embroidery": "Stay close to the embroidery detail.",
                "hand_holding_hat": "Search for a hand holding the hat.",
                "broaden": "Broaden the search.",
            },
        }
    }


def test_build_jev_client_is_disabled_by_default():
    settings = Settings(JEV_ENABLED=False)

    assert build_jev_client(settings) is None


def test_jev_success_records_usage_and_reuses_cache():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/v1/systemone"
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    "next_context": {
                        "type": "choice",
                        "choice": "hand_holding_hat",
                        "confidence": 0.94,
                        "probabilities": {
                            "same_embroidery": 0.03,
                            "hand_holding_hat": 0.94,
                            "broaden": 0.03,
                        },
                    }
                },
                "usage": {"input_tokens": 1000, "output_tokens": 8},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient("test-key", client=http, cache_ttl_seconds=60)

    first = client.evaluate(
        state={"approved": 7, "target": 50},
        questions=_questions(),
    )
    second = client.evaluate(
        state={"approved": 7, "target": 50},
        questions=_questions(),
    )

    assert first.ok is True
    assert first.source == "provider"
    assert first.answers["next_context"]["choice"] == "hand_holding_hat"
    assert first.usage.input_tokens == 1000
    assert first.estimated_cost_micros == 42
    assert second.ok is True
    assert second.source == "cache"
    assert second.cached is True
    assert calls == 1


def test_jev_credit_failure_blocks_repeated_provider_calls():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            402,
            text="Account credit balance exhausted.",
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient(
        "test-key",
        client=http,
        billing_recheck_seconds=3600,
    )

    first = client.evaluate(state={"x": 1}, questions=_questions())
    second = client.evaluate(state={"x": 2}, questions=_questions())

    assert first.ok is False
    assert first.fallback_reason == "billing_blocked"
    assert client.circuit_state == "billing_blocked"
    assert second.ok is False
    assert second.fallback_reason == "billing_blocked"
    assert calls == 1


def test_jev_timeout_opens_circuit_and_falls_back():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient(
        "test-key",
        client=http,
        max_retries=0,
        circuit_failure_threshold=1,
        circuit_open_seconds=300,
    )

    first = client.evaluate(state={"x": 1}, questions=_questions())
    second = client.evaluate(state={"x": 2}, questions=_questions())

    assert first.ok is False
    assert first.fallback_reason == "timeout"
    assert client.circuit_state == "open"
    assert second.ok is False
    assert second.fallback_reason == "circuit_open"
    assert calls == 1


def test_jev_invalid_success_payload_falls_back_without_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "jev-latest"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient("test-key", client=http)

    result = client.evaluate(state={"x": 1}, questions=_questions())

    assert result.ok is False
    assert result.fallback_reason == "invalid_response"


def test_jev_process_budget_fails_open_to_existing_logic():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    "next_context": {
                        "type": "choice",
                        "choice": "broaden",
                        "confidence": 0.8,
                        "probabilities": {
                            "same_embroidery": 0.1,
                            "hand_holding_hat": 0.1,
                            "broaden": 0.8,
                        },
                    }
                },
                "usage": {"input_tokens": 1000, "output_tokens": 4},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient(
        "test-key",
        client=http,
        daily_budget_usd=0.000001,
        monthly_budget_usd=1.0,
    )

    first = client.evaluate(state={"x": 1}, questions=_questions())
    second = client.evaluate(state={"x": 2}, questions=_questions())

    assert first.ok is True
    assert second.ok is False
    assert second.fallback_reason == "daily_budget_exceeded"


def test_jev_rate_limit_falls_back_without_retry_loop():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="rate limited")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient("test-key", client=http, max_retries=1)

    result = client.evaluate(state={"x": 1}, questions=_questions())

    assert result.ok is False
    assert result.fallback_reason == "rate_limit"
    assert result.http_status == 429
    assert calls == 1


def test_jev_retries_one_server_error_then_succeeds():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    "next_context": {
                        "type": "choice",
                        "choice": "same_embroidery",
                        "confidence": 0.91,
                        "probabilities": {
                            "same_embroidery": 0.91,
                            "hand_holding_hat": 0.05,
                            "broaden": 0.04,
                        },
                    }
                },
                "usage": {"input_tokens": 500, "output_tokens": 4},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient("test-key", client=http, max_retries=1)

    result = client.evaluate(state={"x": 1}, questions=_questions())

    assert result.ok is True
    assert result.answers["next_context"]["choice"] == "same_embroidery"
    assert calls == 2


def test_jev_call_can_disable_retries_for_latency_sensitive_shadow_path():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="temporarily unavailable")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient("test-key", client=http, max_retries=3)

    result = client.evaluate(
        state={"x": 1},
        questions=_questions(),
        max_retries=0,
    )

    assert result.ok is False
    assert result.fallback_reason == "provider_unavailable"
    assert calls == 1


def test_jev_credit_marker_on_forbidden_response_enters_billing_block():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, text="Insufficient credits for this request.")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = JevClient("test-key", client=http)

    result = client.evaluate(state={"x": 1}, questions=_questions())

    assert result.ok is False
    assert result.fallback_reason == "billing_blocked"
    assert client.circuit_state == "billing_blocked"
    assert calls == 1
