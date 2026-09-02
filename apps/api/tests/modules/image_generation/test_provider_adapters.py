import asyncio
import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.modules.image_generation.providers import PreparedImage
from app.providers.ai.adobe_firefly import AdobeFireflySquareProvider, FireflyProviderError
from app.providers.ai.cloudflare_workers_ai import CloudflareImageProviderError, CloudflareSquareImageProvider
from app.providers.ai.gemini_image import GeminiImageProviderError, GeminiSquareImageProvider


def png(size: int = 1024) -> bytes:
    output = BytesIO()
    Image.new("RGB", (size, size), "navy").save(output, "PNG")
    return output.getvalue()


SOURCE = PreparedImage(image_bytes=png(16), mime_type="image/png", width=16, height=16)


def test_firefly_raw_upload_nested_id_expand_contract_and_token_cache():
    requests = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "adobelogin" in request.url.host:
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if request.url.path == "/v2/storage/image":
            assert request.headers["content-type"] == "image/png"
            assert request.content == SOURCE.image_bytes
            return httpx.Response(200, json={"images": [{"id": "upload-1"}]})
        assert request.url.path == "/v3/images/expand-async"
        body = __import__("json").loads(request.content)
        assert body["image"]["source"]["uploadId"] == "upload-1"
        assert body["size"] == {"width": 2048, "height": 2048}
        assert body["numVariations"] == 1
        return httpx.Response(
            200,
            json={
                "jobId": "job-1",
                "statusUrl": "https://firefly-api.adobe.io/status/job-1",
                "cancelUrl": "https://firefly-api.adobe.io/status/job-1/cancel",
            },
        )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AdobeFireflySquareProvider(client_id="id", client_secret="secret", http_client=client)
    first = asyncio.run(provider.generate_square(source=SOURCE, target_size=2048, prompt=" widen "))
    second = asyncio.run(provider.generate_square(source=SOURCE, target_size=2048, prompt=None))
    assert first.upload_id == "upload-1"
    assert second.provider_job_id == "job-1"
    assert sum("adobelogin" in request.url.host for request in requests) == 1
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "status,code,retryable",
    [(401, "firefly_auth_failed", False), (429, "firefly_provider_unavailable", True)],
)
def test_firefly_auth_errors(status, code, retryable):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    provider = AdobeFireflySquareProvider(client_id="id", client_secret="secret", http_client=client)
    with pytest.raises(FireflyProviderError) as raised:
        asyncio.run(provider.generate_square(source=SOURCE, target_size=1024, prompt=None))
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    asyncio.run(client.aclose())


def test_firefly_malformed_upload_and_uncertain_submission():
    count = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        if "adobelogin" in request.url.host:
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        count += 1
        if count == 1:
            return httpx.Response(200, json={"images": [{"id": "upload"}]})
        raise httpx.ConnectError("lost", request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AdobeFireflySquareProvider(client_id="id", client_secret="secret", http_client=client)
    with pytest.raises(FireflyProviderError) as raised:
        asyncio.run(provider.generate_square(source=SOURCE, target_size=1024, prompt=None))
    assert raised.value.code == "firefly_submission_uncertain"
    assert raised.value.uncertain
    assert not raised.value.retryable
    asyncio.run(client.aclose())


def test_firefly_poll_complete_failed_and_cancel():
    status = "succeeded"
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "adobelogin" in request.url.host:
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if request.method == "POST":
            return httpx.Response(204)
        if status == "succeeded":
            return httpx.Response(200, json={"status": status, "result": {"images": [{"url": "https://firefly.adobe.com/result.png"}]}})
        return httpx.Response(200, json={"status": status})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AdobeFireflySquareProvider(client_id="id", client_secret="secret", http_client=client)
    result = asyncio.run(provider.poll(status_url="https://firefly-api.adobe.io/jobs/1"))
    assert result.state == "succeeded"
    assert result.output_url == "https://firefly.adobe.com/result.png"
    status = "failed"
    assert asyncio.run(provider.poll(status_url="https://firefly-api.adobe.io/jobs/1")).state == "failed"
    asyncio.run(provider.cancel(cancel_url="https://firefly-api.adobe.io/jobs/1/cancel"))
    assert any(request.method == "POST" and request.url.path.endswith("/cancel") for request in calls)
    asyncio.run(client.aclose())


def test_cloudflare_img2img_payload_output_and_rate_limit():
    output = png(1024)
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, headers={"content-type": "image/png", "cf-ray": "ray-1"}, content=output)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CloudflareSquareImageProvider(account_id="account", api_token="token", http_client=client)
    result = asyncio.run(provider.generate_square(source=SOURCE, target_size=1024, prompt="add sky"))
    assert seen["request"].url.path.endswith("/accounts/account/ai/run/@cf/runwayml/stable-diffusion-v1-5-inpainting")
    assert seen["request"].headers["authorization"] == "Bearer token"
    assert seen["body"]["width"] == 1024
    assert seen["body"]["height"] == 1024
    assert seen["body"]["image_b64"]
    assert seen["body"]["mask"]
    assert result.model == "@cf/runwayml/stable-diffusion-v1-5-inpainting"
    assert result.provider_request_id == "ray-1"
    asyncio.run(client.aclose())

    limited = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    provider = CloudflareSquareImageProvider(account_id="account", api_token="token", http_client=limited)
    with pytest.raises(CloudflareImageProviderError) as raised:
        asyncio.run(provider.generate_square(source=SOURCE, target_size=1024, prompt=None))
    assert raised.value.code == "cloudflare_sd_rate_limited"
    assert raised.value.retryable
    asyncio.run(limited.aclose())


@pytest.mark.parametrize("target,image_size", [(1024, "1K"), (2048, "2K")])
def test_gemini_model_payload_and_output(target, image_size):
    output = png(target)
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        body = __import__("json").loads(request.content)
        seen["body"] = body
        return httpx.Response(
            200,
            headers={"x-goog-request-id": "request-1"},
            json={
                "candidates": [{
                    "content": {
                        "parts": [{"inlineData": {"mimeType": "image/png", "data": base64.b64encode(output).decode()}}]
                    }
                }]
            },
        )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSquareImageProvider(api_key="secret", http_client=client)
    result = asyncio.run(provider.generate_square(source=SOURCE, target_size=target, prompt="keep beach"))
    request = seen["request"]
    body = seen["body"]
    assert request.url.path.endswith("/models/gemini-3.1-flash-image:generateContent")
    assert request.headers["x-goog-api-key"] == "secret"
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "1:1", "imageSize": image_size}
    assert body["contents"][0]["parts"][1]["inlineData"]["data"] == base64.b64encode(SOURCE.image_bytes).decode()
    assert "User preference:\nkeep beach" in body["contents"][0]["parts"][0]["text"]
    assert result.model == "gemini-3.1-flash-image"
    assert result.provider_request_id == "request-1"
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "status,code,retryable",
    [(429, "gemini_image_rate_limited", True), (503, "gemini_image_provider_unavailable", True), (403, "gemini_image_auth_failed", False)],
)
def test_gemini_errors(status, code, retryable):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    provider = GeminiSquareImageProvider(api_key="secret", http_client=client)
    with pytest.raises(GeminiImageProviderError) as raised:
        asyncio.run(provider.generate_square(source=SOURCE, target_size=1024, prompt=None))
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    asyncio.run(client.aclose())


def test_gemini_rate_limit_keeps_bounded_provider_diagnostic():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                429,
                json={"error": {"message": "Quota exceeded for GenerateContent requests."}},
            )
        )
    )
    provider = GeminiSquareImageProvider(api_key="secret", http_client=client)
    with pytest.raises(GeminiImageProviderError) as raised:
        asyncio.run(provider.generate_square(source=SOURCE, target_size=1024, prompt=None))
    assert raised.value.code == "gemini_image_rate_limited"
    assert str(raised.value) == "Quota exceeded for GenerateContent requests."
    asyncio.run(client.aclose())


def test_gemini_rejects_no_image_and_unsupported_mime():
    responses = iter([
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "no"}]}}]}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/gif", "data": base64.b64encode(b"x").decode()}}]}}]}),
    ])
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(responses)))
    provider = GeminiSquareImageProvider(api_key="secret", http_client=client)
    for _ in range(2):
        with pytest.raises(GeminiImageProviderError, match="Gemini returned"):
            asyncio.run(provider.generate_square(source=SOURCE, target_size=1024, prompt=None))
    asyncio.run(client.aclose())
