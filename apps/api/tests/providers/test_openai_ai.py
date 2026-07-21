import base64
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai

from app.domain.providers.contracts import (
    AiMetadataAnalysisInput,
    AiMetadataProvider,
    AiProviderError,
)
from app.providers.ai.openai import OpenAiMetadataProvider


class FakeResponse(SimpleNamespace):
    def model_dump(self, **_kwargs):
        return {
            "id": getattr(self, "id", None),
            "model": getattr(self, "model", None),
            "status": getattr(self, "status", None),
        }


class FakeClient:
    def __init__(self, response=None):
        self.responses = SimpleNamespace(
            create=AsyncMock(return_value=response or completed_response())
        )
        self.close = AsyncMock()


def completed_response(
    text='{"subject":"cat"}',
    *,
    status="completed",
    refusal=None,
    usage=None,
):
    if refusal is not None:
        content = [SimpleNamespace(type="refusal", refusal=refusal)]
    elif text is None:
        content = []
    else:
        content = [SimpleNamespace(type="output_text", text=text)]
    return FakeResponse(
        id="resp_123",
        _request_id="req_123",
        model="openai-test",
        status=status,
        incomplete_details=(
            {"reason": "max_output_tokens"} if status == "incomplete" else None
        ),
        output=[SimpleNamespace(type="message", content=content)],
        usage=usage or {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
        },
    )


def analysis_input(*, schema=None, cancelled=None):
    return AiMetadataAnalysisInput(
        tenant_id="tenant-a",
        asset_id="asset-a",
        prompt="Extract metadata",
        image_bytes=b"bounded-jpeg",
        image_mime_type="image/jpeg",
        metadata_profile="general assets",
        metadata_profile_version="v1",
        json_schema=schema,
        is_cancelled=cancelled,
    )


def provider(client=None, **overrides):
    values = {
        "api_key": "sk-test-secret",
        "model": "openai-test",
        "allowed_models": ("openai-test",),
        "client": client or FakeClient(),
    }
    values.update(overrides)
    return OpenAiMetadataProvider(**values)


class OpenAiMetadataProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_responses_request_uses_bounded_base64_image(self):
        client = FakeClient()
        result = await provider(client).analyze_single(analysis_input())

        request = client.responses.create.await_args.kwargs
        self.assertEqual(request["model"], "openai-test")
        self.assertEqual(request["store"], False)
        self.assertEqual(request["timeout"], 60.0)
        content = request["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_text")
        self.assertIn("Extract metadata", content[0]["text"])
        self.assertEqual(content[1]["type"], "input_image")
        self.assertEqual(content[1]["detail"], "auto")
        self.assertEqual(
            content[1]["image_url"],
            "data:image/jpeg;base64,"
            + base64.b64encode(b"bounded-jpeg").decode("ascii"),
        )
        self.assertEqual(result.metadata, {"subject": "cat"})

    async def test_compatible_schema_uses_strict_structured_output(self):
        schema = {
            "type": "object",
            "properties": {"subject": {"type": "string"}},
            "required": ["subject"],
        }
        client = FakeClient()
        await provider(client).analyze_single(analysis_input(schema=schema))

        format_config = client.responses.create.await_args.kwargs["text"]["format"]
        self.assertEqual(format_config["type"], "json_schema")
        self.assertTrue(format_config["strict"])
        self.assertEqual(format_config["schema"]["additionalProperties"], False)
        self.assertRegex(format_config["name"], r"^[A-Za-z0-9_-]{1,64}$")
        self.assertNotIn("additionalProperties", schema)

    async def test_profile_without_compatible_schema_uses_json_object_mode(self):
        client = FakeClient()
        await provider(client).analyze_single(analysis_input())
        request = client.responses.create.await_args.kwargs
        self.assertEqual(request["text"]["format"], {"type": "json_object"})
        self.assertIn("exactly one JSON object", request["input"][0]["content"][0]["text"])

    async def test_maps_usage_request_id_model_status_and_raw_retention(self):
        response = completed_response(
            usage={
                "input_tokens": 13,
                "output_tokens": 5,
                "total_tokens": 18,
                "input_tokens_details": {"cached_tokens": 2},
            }
        )
        result = await provider(
            FakeClient(response), capture_raw_response=True
        ).analyze_single(analysis_input())

        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "openai-test")
        self.assertEqual(result.provider_request_id, "req_123")
        self.assertEqual(result.usage["input_tokens"], 13)
        self.assertEqual(result.usage["output_tokens"], 5)
        self.assertEqual(result.usage["input_tokens_details"]["cached_tokens"], 2)
        self.assertEqual(result.provider_metadata["status"], "completed")
        self.assertIsInstance(result.provider_metadata["latency_ms"], int)
        self.assertEqual(result.raw_response["id"], "resp_123")

        not_retained = await provider(
            FakeClient(response), capture_raw_response=False
        ).analyze_single(analysis_input())
        self.assertIsNone(not_retained.raw_response)

    async def test_invalid_json_and_non_object_are_retryable(self):
        for text, code in (
            ("not-json", "openai_invalid_json"),
            ("[]", "openai_invalid_document"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(AiProviderError) as raised:
                    await provider(
                        FakeClient(completed_response(text))
                    ).analyze_single(analysis_input())
                self.assertEqual(raised.exception.code, code)
                self.assertTrue(raised.exception.retryable)

    async def test_empty_refusal_and_incomplete_responses_are_classified(self):
        cases = (
            (completed_response(None), "openai_empty_response", True),
            (
                completed_response(refusal="policy"),
                "openai_refusal",
                False,
            ),
            (
                completed_response("{}", status="incomplete"),
                "openai_incomplete_response",
                True,
            ),
        )
        for response, code, retryable in cases:
            with self.subTest(code=code):
                with self.assertRaises(AiProviderError) as raised:
                    await provider(FakeClient(response)).analyze_single(
                        analysis_input()
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertIs(raised.exception.retryable, retryable)

    async def test_transport_and_api_errors_have_stable_safe_codes(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        cases = (
            (
                openai.APITimeoutError(request),
                "openai_timeout",
                True,
            ),
            (
                openai.APIConnectionError(request=request),
                "openai_connection_error",
                True,
            ),
            (
                openai.RateLimitError(
                    "secret rate body",
                    response=httpx.Response(429, request=request),
                    body={"error": {"message": "secret rate body"}},
                ),
                "openai_rate_limit",
                True,
            ),
            (
                openai.AuthenticationError(
                    "sk-test-secret is invalid",
                    response=httpx.Response(401, request=request),
                    body={"error": {"message": "sk-test-secret is invalid"}},
                ),
                "openai_authentication_failed",
                False,
            ),
            (
                openai.InternalServerError(
                    "temporary server failure",
                    response=httpx.Response(503, request=request),
                    body={"error": {"message": "temporary server failure"}},
                ),
                "openai_service_unavailable",
                True,
            ),
        )
        for error, code, retryable in cases:
            with self.subTest(code=code):
                client = FakeClient()
                client.responses.create.side_effect = error
                with self.assertRaises(AiProviderError) as raised:
                    await provider(client).analyze_single(analysis_input())
                self.assertEqual(raised.exception.code, code)
                self.assertIs(raised.exception.retryable, retryable)
                self.assertNotIn("sk-test-secret", str(raised.exception))

    async def test_invalid_schema_and_unsupported_image_are_non_retryable(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        client = FakeClient()
        client.responses.create.side_effect = openai.BadRequestError(
            "invalid json_schema",
            response=httpx.Response(400, request=request),
            body={"error": {"message": "invalid json_schema"}},
        )
        with self.assertRaises(AiProviderError) as raised:
            await provider(client).analyze_single(analysis_input())
        self.assertEqual(raised.exception.code, "openai_invalid_schema")
        self.assertFalse(raised.exception.retryable)

        bad_image = analysis_input()
        bad_image = replace(
            bad_image, image_mime_type="application/octet-stream")
        with self.assertRaises(AiProviderError) as raised:
            await provider().analyze_single(bad_image)
        self.assertEqual(raised.exception.code, "openai_unsupported_image")
        self.assertFalse(raised.exception.retryable)

    async def test_cancellation_is_checked_before_and_after_request(self):
        with self.assertRaises(AiProviderError) as raised:
            await provider().analyze_single(
                analysis_input(cancelled=lambda: True)
            )
        self.assertEqual(raised.exception.code, "analysis_cancelled")

        calls = iter((False, True))
        with self.assertRaises(AiProviderError) as raised:
            await provider().analyze_single(
                analysis_input(cancelled=lambda: next(calls))
            )
        self.assertEqual(raised.exception.code, "analysis_cancelled")

    async def test_batch_capability_is_advertised_but_disabled_by_default(self):
        instance = provider()
        self.assertTrue(instance.supports_batch)
        with self.assertRaises(AiProviderError) as raised:
            await instance.submit_batch(None)
        self.assertEqual(raised.exception.code, "openai_batch_disabled")
        self.assertFalse(raised.exception.retryable)

    async def test_model_allowlist_and_key_redaction(self):
        with self.assertRaises(ValueError):
            OpenAiMetadataProvider(
                "sk-test-secret",
                model="disallowed",
                allowed_models=("allowed",),
                client=FakeClient(),
            )
        instance = provider()
        self.assertIsInstance(instance, AiMetadataProvider)
        self.assertNotIn("sk-test-secret", repr(instance))
        self.assertEqual(instance.default_model, "openai-test")
