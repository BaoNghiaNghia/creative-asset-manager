import json
import unittest

import httpx

from app.domain.providers.contracts import AiMetadataAnalysisInput, AiProviderError
from app.providers.ai.gemini import GeminiAiMetadataProvider


def analysis_input():
    return AiMetadataAnalysisInput(
        tenant_id="tenant-a",
        asset_id="asset-a",
        prompt="Return metadata",
        image_bytes=b"jpeg",
        image_mime_type="image/jpeg",
        metadata_profile="general",
        metadata_profile_version="1",
    )


class GeminiAiMetadataProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_builds_structured_multimodal_request_and_captures_audit(self):
        async def handler(request):
            self.assertNotIn("key=", str(request.url))
            self.assertEqual(request.headers["x-goog-api-key"], "secret")
            body = json.loads(request.content)
            self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
            self.assertEqual(body["contents"][0]["parts"][0]["text"], "Return metadata")
            return httpx.Response(
                200,
                json={
                    "candidates": [{
                        "content": {"parts": [{"text": '{"subject":"cat"}'}]},
                        "finishReason": "STOP",
                    }],
                    "usageMetadata": {"totalTokenCount": 9},
                    "modelVersion": "gemini-test",
                    "responseId": "request-1",
                },
            )

        provider = GeminiAiMetadataProvider(
            "secret", model="gemini-test",
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())
        self.assertEqual(result.metadata, {"subject": "cat"})
        self.assertEqual(result.provider_request_id, "request-1")
        self.assertEqual(result.usage["totalTokenCount"], 9)

    async def test_malformed_and_empty_output_are_retryable(self):
        for payload, code in (
            ({"candidates": [{"content": {"parts": [{"text": "nope"}]}}]}, "gemini_invalid_json"),
            ({"candidates": []}, "gemini_empty_response"),
        ):
            async def handler(_request, payload=payload):
                return httpx.Response(200, json=payload)
            provider = GeminiAiMetadataProvider(
                "secret", transport=httpx.MockTransport(handler)
            )
            with self.assertRaises(AiProviderError) as raised:
                await provider.analyze_single(analysis_input())
            self.assertEqual(raised.exception.code, code)
            self.assertTrue(raised.exception.retryable)

    async def test_rate_limit_is_retryable_and_bad_request_is_permanent(self):
        for status, retryable in ((429, True), (400, False)):
            async def handler(_request, status=status):
                return httpx.Response(status, json={"error": {}})
            provider = GeminiAiMetadataProvider(
                "secret", transport=httpx.MockTransport(handler)
            )
            with self.assertRaises(AiProviderError) as raised:
                await provider.analyze_single(analysis_input())
            self.assertIs(raised.exception.retryable, retryable)

    async def test_timeout_is_retryable(self):
        async def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)
        provider = GeminiAiMetadataProvider(
            "secret", transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(AiProviderError) as raised:
            await provider.analyze_single(analysis_input())
        self.assertEqual(raised.exception.code, "gemini_transport_error")
        self.assertTrue(raised.exception.retryable)
