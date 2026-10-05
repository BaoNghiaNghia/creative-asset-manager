from __future__ import annotations

import asyncio

import httpx

from app.domain.providers.contracts import AiMetadataAnalysisResult
from app.modules.realistic_review_ugc.quote_scout_analysis import (
    QuoteScoutError,
    analyze_hat_quote,
    validate_pinterest_image_url,
)


class FakeQuoteProvider:
    provider_name = "gemini"
    supports_single = True
    supports_batch = False
    default_model = "gemini-test"

    def __init__(self) -> None:
        self.inputs = []

    async def analyze_single(self, input):
        self.inputs.append(input)
        return AiMetadataAnalysisResult(
            metadata={
                "is_hat": True,
                "quotes": [
                    "  Bad Day To Be A Hotdog  ",
                    "Bad Day To Be A Hotdog",
                ],
                "confidence": 0.94,
            },
            provider="gemini",
            model="gemini-test",
        )


def test_validate_pinterest_image_url_only_accepts_pinimg_https():
    assert (
        validate_pinterest_image_url(
            "https://i.pinimg.com/originals/aa/bb/example.jpg"
        )
        == "https://i.pinimg.com/originals/aa/bb/example.jpg"
    )
    for value in (
        "http://i.pinimg.com/originals/a.jpg",
        "https://example.com/a.jpg",
        "https://pinimg.com.evil.example/a.jpg",
    ):
        try:
            validate_pinterest_image_url(value)
        except QuoteScoutError as exc:
            assert exc.code == "quote_scout_image_url_rejected"
        else:
            raise AssertionError("unsafe URL was accepted")


def test_analyze_hat_quote_downloads_pinimg_and_normalizes_visible_quote():
    requests: list[httpx.Request] = []

    async def image_provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "image/jpeg"},
            content=b"fake-jpeg-bytes",
        )

    async def scenario() -> None:
        provider = FakeQuoteProvider()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(image_provider)
        ) as client:
            result = await analyze_hat_quote(
                provider=provider,
                tenant_id="tenant-a",
                image_url="https://i.pinimg.com/originals/aa/bb/example.jpg",
                pin_url="https://www.pinterest.com/pin/123/",
                alt_text="Hat photo with unrelated Pinterest caption",
                http_client=client,
            )

        assert result.quotes == ["Bad Day To Be A Hotdog"]
        assert result.confidence == 0.94
        assert result.provider == "gemini"
        assert len(provider.inputs) == 1
        analysis_input = provider.inputs[0]
        assert analysis_input.image_bytes == b"fake-jpeg-bytes"
        assert analysis_input.image_mime_type == "image/jpeg"
        assert "only wording physically on the hat/cap" in analysis_input.prompt
        assert "unrelated Pinterest caption" in analysis_input.prompt
        assert len(requests) == 1

    asyncio.run(scenario())
