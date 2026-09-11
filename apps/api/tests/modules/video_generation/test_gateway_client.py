import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.modules.video_generation.gateway_client import (
    DolaGatewayClient,
    DolaGatewayError,
    GatewayReference,
    validate_gateway_url,
)


def settings(url="http://127.0.0.1:8100", key="test-key"):
    return SimpleNamespace(
        DOLA_RENDER_GATEWAY_URL=url,
        DOLA_RENDER_GATEWAY_INTERNAL_KEY=key,
        DOLA_RENDER_GATEWAY_TIMEOUT_SECONDS=10,
        DOLA_RENDER_GATEWAY_CONTENT_TIMEOUT_SECONDS=30,
    )


def run(awaitable):
    return asyncio.run(awaitable)


def test_submit_uses_bearer_stable_key_and_ordered_multipart():
    captured = {}
    def responder(request):
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200, json={"generation_id": "gateway-1", "status": "accepted"})
    client = DolaGatewayClient(settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(responder)))
    result = run(client.submit(
        run_id="cam-run", prompt="private prompt", model="seedance-2.0",
        aspect_ratio="16:9", duration_seconds=10,
        references=[
            GatewayReference("reference-0.png", "image/png", b"first-bytes"),
            GatewayReference("reference-1.jpg", "image/jpeg", b"second-bytes"),
        ],
    ))
    assert result.generation_id == "gateway-1"
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["headers"]["idempotency-key"] == "video-generate:cam-run"
    body = captured["body"]
    assert body.index(b"first-bytes") < body.index(b"second-bytes")
    assert b'"model":"seedance-2.0"' in body
    assert b'"aspect_ratio":"16:9"' in body
    run(client.aclose())


@pytest.mark.parametrize("status,retryable,code", [
    (401, False, "dola_gateway_auth_failed"),
    (409, False, "dola_idempotency_key_conflict"),
    (502, True, "dola_gateway_unavailable"),
    (503, True, "dola_gateway_unavailable"),
    (504, True, "dola_gateway_unavailable"),
])
def test_http_errors_are_classified(status, retryable, code):
    client = DolaGatewayClient(
        settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    )
    with pytest.raises(DolaGatewayError) as failure:
        run(client.get_generation("gateway-1"))
    assert (failure.value.code, failure.value.retryable) == (code, retryable)
    assert "test-key" not in str(failure.value)
    run(client.aclose())


def test_status_parsed_and_malformed_response_rejected():
    client = DolaGatewayClient(
        settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"generation_id": "gateway-1", "status": "running"})
        ))
    )
    assert run(client.get_generation("gateway-1")).status == "running"
    run(client.aclose())
    broken = DolaGatewayClient(
        settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"generation_id": "gateway-1", "status": "impossible"})
        ))
    )
    with pytest.raises(DolaGatewayError, match="invalid response"):
        run(broken.get_generation("gateway-1"))
    run(broken.aclose())


@pytest.mark.parametrize("url", ["https://example.com", "http://10.0.0.1:8100", "http://user:pass@127.0.0.1:8100"])
def test_non_loopback_urls_are_rejected(url):
    with pytest.raises(DolaGatewayError, match="loopback"):
        validate_gateway_url(url)


def test_missing_bearer_fails_closed_without_leaking():
    client = DolaGatewayClient(settings(key=""), client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)))
    with pytest.raises(DolaGatewayError) as failure:
        run(client.get_generation("gateway-1"))
    assert failure.value.code == "dola_gateway_not_configured"
    run(client.aclose())
