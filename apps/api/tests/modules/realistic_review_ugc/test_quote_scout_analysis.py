from __future__ import annotations

import asyncio

import httpx

from app.domain.providers.contracts import AiMetadataAnalysisResult, AiProviderError
from app.modules.realistic_review_ugc.quote_scout_analysis import (
    HatQuoteDocument,
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


def test_hat_quote_document_accepts_gemini_native_phrase_shape():
    document = HatQuoteDocument.model_validate(
        {
            "has_hat": True,
            "phrases": [
                {"text": "Out of Office", "confidence": 1.0},
                {"text": "  Out of Office  ", "confidence": 0.91},
            ],
        }
    )
    assert document.is_hat is True
    assert document.quotes == ["Out of Office"]
    assert document.confidence == 1.0


def test_hat_quote_document_accepts_sayings_alias_and_ignores_extra_metadata():
    document = HatQuoteDocument.model_validate(
        {
            "has_target_cap": True,
            "sayings": ["OUT OF OFFICE", "  BAD DAY TO BE A HOTDOG  "],
            "product_type": "trucker_cap",
            "hat_count": 2,
        }
    )

    assert document.is_hat is True
    assert document.quotes == ["OUT OF OFFICE", "BAD DAY TO BE A HOTDOG"]
    assert document.confidence == 0.0


def test_hat_quote_document_rejects_short_sayings_without_error():
    document = HatQuoteDocument.model_validate(
        {
            "has_hat": True,
            "sayings": ["BOOM"],
            "unexpected_provider_field": {"safe": "ignored"},
        }
    )

    assert document.is_hat is True
    assert document.quotes == []


def test_hat_quote_document_keeps_all_distinct_quotes_from_many_hats():
    quotes = [
        "BAD DAY TO BE A HOTDOG",
        "OUT OF OFFICE",
        "GIRLS CAN GOLF TOO",
        "WILD AT HEART",
        "NEED MONEY FOR DIRTBIKES",
        "WHY TAKE THE HIGH ROAD",
        "EVERY DREAM BEGINS WITH A WISH",
        "PLEASE BE PATIENT WITH ME",
    ]
    document = HatQuoteDocument.model_validate(
        {
            "is_hat": True,
            "quotes": quotes,
            "confidence": 0.97,
        }
    )

    assert document.quotes == quotes
    assert len(document.quotes) == 8


def test_hat_quote_document_groups_multiline_text_by_physical_cap():
    document = HatQuoteDocument.model_validate(
        {
            "is_hat": True,
            "cap_texts": [
                {
                    "lines": [
                        "GIRLS CAN GOLF TOO",
                        "golf club",
                        "SPORTY & RICH",
                    ]
                },
                {"lines": ["OUT OF OFFICE"]},
            ],
            "confidence": 0.99,
        }
    )

    assert document.quotes == [
        "GIRLS CAN GOLF TOO golf club SPORTY & RICH",
        "OUT OF OFFICE",
    ]
    schema = HatQuoteDocument.model_json_schema()
    assert "cap_texts" in schema["properties"]
    assert "quotes" not in schema["properties"]


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
        assert analysis_input.metadata_profile_version == "rrugc-quote-scout-v7"
        assert "cap_texts" in analysis_input.json_schema["properties"]
        assert "quotes" not in analysis_input.json_schema["properties"]
        assert "EVERY readable text line physically printed or embroidered on that SAME cap" in analysis_input.prompt
        assert "Do not stop after the first, clearest, largest, or central target cap." in analysis_input.prompt
        assert "create exactly ONE item in cap_texts" in analysis_input.prompt
        assert "GIRLS CAN GOLF TOO golf club SPORTY & RICH" in analysis_input.prompt
        assert "TARGET CAP" in analysis_input.prompt
        assert "bucket hat" in analysis_input.prompt
        assert "cowboy/western hat" in analysis_input.prompt
        assert "any other non-cap product" in analysis_input.prompt
        assert "unrelated Pinterest caption" in analysis_input.prompt
        assert "IMAGE PRIORITY SCORE" in analysis_input.prompt
        assert "Score 0.90-1.00 for preferred examples" in analysis_input.prompt
        assert "front/front-three-quarter crown is visible" in analysis_input.prompt
        assert "complete quote can be read without guessing" in analysis_input.prompt
        assert "Score below 0.60" in analysis_input.prompt
        assert "cafe, street, store, mirror/selfie, or top-down selfie scene" in analysis_input.prompt
        assert "Worn-cap imagery is not penalized" in analysis_input.prompt
        assert len(requests) == 1

    asyncio.run(scenario())


def test_analyze_hat_quote_fails_over_to_backup_credential_without_redownloading_image():
    requests: list[httpx.Request] = []

    async def image_provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "image/jpeg"},
            content=b"fake-jpeg-bytes",
        )

    class FailoverQuoteProvider:
        provider_name = "gemini"
        supports_single = True
        supports_batch = False
        default_model = "gemini-test"

        def __init__(self) -> None:
            self.credentials: list[str | None] = []

        async def analyze_single(self, input):
            self.credentials.append(input.preferred_credential_provider)
            if input.preferred_credential_provider == "gemini":
                raise AiProviderError(
                    "No Gemini model is currently available.",
                    code="gemini_model_pool_temporarily_unavailable",
                    retryable=True,
                    status_code=503,
                )
            return AiMetadataAnalysisResult(
                metadata={
                    "is_hat": True,
                    "quotes": ["OUT OF OFFICE"],
                    "confidence": 0.98,
                },
                provider="gemini",
                model="gemini-test",
            )

    async def scenario() -> None:
        provider = FailoverQuoteProvider()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(image_provider)
        ) as client:
            result = await analyze_hat_quote(
                provider=provider,
                tenant_id="tenant-a",
                image_url="https://i.pinimg.com/originals/aa/bb/example.jpg",
                pin_url="https://www.pinterest.com/pin/123/",
                alt_text="trucker cap",
                http_client=client,
                credential_providers=("gemini", "gemini_backup_1"),
            )

        assert result.quotes == ["OUT OF OFFICE"]
        assert provider.credentials == ["gemini", "gemini_backup_1"]
        assert len(requests) == 1

    asyncio.run(scenario())


def test_analyze_hat_quote_does_not_fail_over_nonretryable_provider_error():
    class TerminalQuoteProvider(FakeQuoteProvider):
        def __init__(self) -> None:
            super().__init__()
            self.credentials: list[str | None] = []

        async def analyze_single(self, input):
            self.credentials.append(input.preferred_credential_provider)
            raise AiProviderError(
                "Invalid request.",
                code="gemini_invalid_document",
                retryable=False,
                status_code=422,
            )

    async def image_provider(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "image/jpeg"},
            content=b"fake-jpeg-bytes",
        )

    async def scenario() -> None:
        provider = TerminalQuoteProvider()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(image_provider)
        ) as client:
            try:
                await analyze_hat_quote(
                    provider=provider,
                    tenant_id="tenant-a",
                    image_url="https://i.pinimg.com/originals/aa/bb/example.jpg",
                    pin_url="https://www.pinterest.com/pin/123/",
                    alt_text="trucker cap",
                    http_client=client,
                    credential_providers=("gemini", "gemini_backup_1"),
                )
            except AiProviderError as exc:
                assert exc.code == "gemini_invalid_document"
            else:
                raise AssertionError("nonretryable provider error was not raised")

        assert provider.credentials == ["gemini"]

    asyncio.run(scenario())
