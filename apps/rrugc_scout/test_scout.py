import asyncio

import httpx

from scout import (
    AutoScoutClient,
    access_gate,
    allowed_image,
    allowed_pin,
    extract_visible,
    normalize_candidates,
)


def test_url_allowlists():
    assert allowed_pin("https://www.pinterest.com/pin/123/")
    assert allowed_pin("https://pinterest.com/pin/abc/")
    assert not allowed_pin("https://www.pinterest.com/search/pins/?q=abc")
    assert not allowed_pin("https://evil.example/pin/123/")
    assert allowed_image("https://i.pinimg.com/736x/a/b/c.jpg")
    assert not allowed_image("https://example.com/image.jpg")


def test_normalize_candidates_filters_and_dedupes():
    rows = [
        {
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/a.jpg",
            "alt_text": "one",
        },
        {
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/a.jpg",
            "alt_text": "duplicate",
        },
        {
            "pin_url": "https://evil.example/pin/9/",
            "image_url": "https://i.pinimg.com/b.jpg",
        },
    ]
    result = normalize_candidates(rows)
    assert len(result) == 1
    assert result[0].alt_text == "one"


def test_extract_visible_uses_normalization_contract():
    class FakePage:
        async def evaluate(self, _script):
            return [
                {
                    "pin_url": "https://www.pinterest.com/pin/123/",
                    "image_url": "https://i.pinimg.com/736x/a.jpg",
                    "alt_text": "large visible result",
                },
                {
                    "pin_url": "https://evil.example/pin/1/",
                    "image_url": "https://i.pinimg.com/736x/b.jpg",
                },
            ]

    rows = asyncio.run(extract_visible(FakePage()))
    assert len(rows) == 1
    assert rows[0].image_url.endswith("/736x/a.jpg")


def test_access_gate_detects_login_and_challenge_without_solving_them():
    class LoginPage:
        url = "https://www.pinterest.com/login/"

        async def evaluate(self, _script):
            return False

    class CaptchaPage:
        url = "https://www.pinterest.com/search/pins/?q=test"

        async def evaluate(self, _script):
            return True

    assert asyncio.run(access_gate(LoginPage())) == "login"
    assert asyncio.run(access_gate(CaptchaPage())) == "challenge"


def test_auto_scout_client_uses_agent_scoped_endpoints():
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/claim"):
            assert request.headers["x-scout-version"] == "rrugc-scout-v2"
            assert request.headers["x-scout-machine"] == "studio-pc"
            return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})
        return httpx.Response(200, json={"status": "ready"})

    async def scenario():
        client = AutoScoutClient(
            "https://cam.example",
            "agent-1",
            "secret-token",
            machine_label="studio-pc",
        )
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            headers={"Authorization": "Bearer secret-token"},
            transport=httpx.MockTransport(handler),
        )
        try:
            assert await client.claim() is None
            await client.heartbeat("ready")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert requests == [
        ("POST", "/api/v1/realistic-review-ugc/scout-agents/agent-1/claim"),
        ("POST", "/api/v1/realistic-review-ugc/scout-agents/agent-1/heartbeat"),
    ]
