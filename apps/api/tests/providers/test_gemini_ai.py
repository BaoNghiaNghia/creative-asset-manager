import json
import unittest
from datetime import datetime, timedelta, timezone

import asyncio
import httpx

from app.domain.providers.contracts import AiMetadataAnalysisInput, AiProviderError
from app.providers.ai.gemini import GeminiAiMetadataProvider, GeminiModelLimit


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


async def _immediate():
    return None


def configured_gemini_provider(
    api_key: str,
    *,
    model: str = "gemini-2.5-flash",
    model_pool: tuple[str, ...] | None = None,
    model_limits: dict[str, GeminiModelLimit] | None = None,
    **kwargs,
) -> GeminiAiMetadataProvider:
    pool = model_pool or (model,)
    return GeminiAiMetadataProvider(
        api_key,
        model=model,
        model_pool=model_pool,
        model_limits=model_limits or {
            name: GeminiModelLimit(rpm=12, tpm=200000, rpd=400)
            for name in pool
        },
        **kwargs,
    )


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=self.seconds
        )

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


class GeminiAiMetadataProviderTest(unittest.IsolatedAsyncioTestCase):
    def test_missing_model_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Gemini model limits are required"):
            GeminiAiMetadataProvider("secret", model="gemini-test")

    def test_tuple_model_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicit GeminiModelLimit"):
            GeminiAiMetadataProvider(
                "secret",
                model="gemini-test",
                model_limits={"gemini-test": (12, 400)},  # type: ignore[dict-item]
            )
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

        provider = configured_gemini_provider(
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
            provider = configured_gemini_provider(
                "secret", transport=httpx.MockTransport(handler)
            )
            with self.assertRaises(AiProviderError) as raised:
                await provider.analyze_single(analysis_input())
            self.assertEqual(raised.exception.code, code)
            self.assertTrue(raised.exception.retryable)

    async def test_rate_limit_is_retryable_and_bad_request_is_permanent(self):
        delays = []

        async def sleeper(delay):
            delays.append(delay)

        for status, retryable in ((429, True), (400, False)):
            async def handler(_request, status=status):
                return httpx.Response(status, json={"error": {}})
            provider = configured_gemini_provider(
                "secret",
                sleeper=sleeper,
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(AiProviderError) as raised:
                await provider.analyze_single(analysis_input())
            self.assertIs(raised.exception.retryable, retryable)

        self.assertEqual(delays, [])

    async def test_timeout_is_retryable(self):
        async def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)
        provider = configured_gemini_provider(
            "secret", transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(AiProviderError) as raised:
            await provider.analyze_single(analysis_input())
        self.assertEqual(raised.exception.code, "gemini_transport_error")
        self.assertTrue(raised.exception.retryable)

    async def test_daily_quota_fails_over_in_priority_order_and_records_audit(self):
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                return httpx.Response(
                    429,
                    json={"error": {"message": "quota exceeded per day"}},
                )
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={"gemini-first": GeminiModelLimit(rpm=12, tpm=100, rpd=400), "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=400)},
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(result.model, "gemini-second")
        self.assertEqual(result.provider_metadata["requested_model"], "gemini-first")
        self.assertEqual(result.provider_metadata["actual_model"], "gemini-second")
        self.assertEqual(result.provider_metadata["attempted_models"], ["gemini-first", "gemini-second"])
        self.assertIn("daily_quota_exhausted", result.provider_metadata["failover_reason"])

    async def test_rate_limit_tries_next_model_without_sleep(self):
        seen = []
        delays = []
        clock = FakeClock()

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                return httpx.Response(
                    429,
                    headers={"retry-after": "30"},
                    json={"error": {"message": "rate limit per minute"}},
                )
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        async def sleeper(delay):
            delays.append(delay)

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=12, tpm=100, rpd=400),
                "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=400),
            },
            sleeper=sleeper,
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(delays, [])
        self.assertEqual(result.model, "gemini-second")
        self.assertIn("gemini-first:rate_limited", result.provider_metadata["failover_reason"])

    async def test_tpm_limit_uses_next_available_model(self):
        seen = []
        clock = FakeClock()

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=12, tpm=4, rpd=400),
                "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=400),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-second"])
        self.assertEqual(result.model, "gemini-second")
        self.assertIn("gemini-first:tpm_limit_reached", result.provider_metadata["failover_reason"])

    async def test_rpm_limit_uses_next_available_model(self):
        seen = []
        clock = FakeClock()

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=1, tpm=100, rpd=400),
                "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=400),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )

        await provider.analyze_single(analysis_input())
        second = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(second.model, "gemini-second")
        self.assertIn(
            "gemini-first:rpm_limit_reached",
            second.provider_metadata["failover_reason"],
        )
    async def test_tpm_reservation_reconciles_prompt_usage(self):
        seen = []
        clock = FakeClock()

        async def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "usageMetadata": {"promptTokenCount": 1},
                "modelVersion": "gemini-first",
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=2, tpm=6, rpd=2),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )

        await provider.analyze_single(analysis_input())
        await provider.analyze_single(analysis_input())

        self.assertEqual(len(seen), 2)
        self.assertEqual(
            [item.tokens for item in provider._runtime["gemini-first"].recent_input_tokens],
            [1, 1],
        )

    async def test_tpm_reservation_expires_from_the_rolling_window(self):
        seen = []
        clock = FakeClock()

        async def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": "gemini-first",
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=2, tpm=5, rpd=2),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )

        await provider.analyze_single(analysis_input())
        clock.advance(60)
        await provider.analyze_single(analysis_input())

        self.assertEqual(len(seen), 2)
    async def test_bad_request_does_not_fail_over(self):
        seen = []

        async def handler(request):
            seen.append(request.url.path)
            return httpx.Response(400, json={"error": {"message": "bad image"}})

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(AiProviderError) as raised:
            await provider.analyze_single(analysis_input())

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(len(seen), 1)
        self.assertEqual(raised.exception.details["attempted_models"], ["gemini-first"])

    async def test_per_model_daily_limit_uses_next_model_without_job_retry(self):
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={"gemini-first": GeminiModelLimit(rpm=12, tpm=100, rpd=1), "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=1)},
            transport=httpx.MockTransport(handler),
        )
        await provider.analyze_single(analysis_input())
        second = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(second.model, "gemini-second")

    async def test_service_unavailable_cools_down_model_and_uses_next_model(self):
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                return httpx.Response(503, json={"error": {"message": "unavailable"}})
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(result.model, "gemini-second")
        self.assertIn("gemini-first:cooldown", result.provider_metadata["failover_reason"])

    async def test_only_one_request_per_model_is_in_flight(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                first_started.set()
                await release_first.wait()
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            transport=httpx.MockTransport(handler),
        )
        first = asyncio.create_task(provider.analyze_single(analysis_input()))
        await first_started.wait()
        second = await provider.analyze_single(analysis_input())
        release_first.set()
        await first

        self.assertEqual(second.model, "gemini-second")
        self.assertEqual(seen.count("gemini-first"), 1)
        self.assertEqual(seen.count("gemini-second"), 1)


    async def test_batch_submit_recovers_ambiguous_transport_by_display_name(self):
        import tempfile
        from pathlib import Path
        from app.domain.providers.contracts import AiBatchSubmissionInput

        list_calls=0
        async def handler(request):
            nonlocal list_calls
            if request.method=="GET":
                list_calls+=1
                if list_calls==1:
                    return httpx.Response(200,json={"operations":[]})
                return httpx.Response(200,json={"operations":[{
                    "name":"batches/recovered",
                    "response":{"displayName":"stable-name",
                        "state":"BATCH_STATE_PENDING"}
                }]})
            raise httpx.ReadTimeout("lost response",request=request)

        provider=configured_gemini_provider(
            "secret",model="gemini-test",transport=httpx.MockTransport(handler))
        with tempfile.NamedTemporaryFile("w",delete=False) as handle:
            json.dump({"custom_item_id":"item-1","prompt":"analyze",
                "image_mime_type":"image/jpeg","image_base64":"YWJj",
                "metadata_profile":"general","metadata_profile_version":"1"},handle)
            handle.write("\n");path=handle.name
        try:
            result=await provider.submit_batch(AiBatchSubmissionInput(
                "tenant-a","stable-key","stable-name","gemini-test",path,1,100))
            self.assertEqual(result.provider_batch_id,"batches/recovered")
            self.assertEqual(result.state,"pending")
        finally:
            Path(path).unlink(missing_ok=True)

    async def test_batch_submit_status_stream_and_cancel(self):
        import tempfile
        from pathlib import Path
        from app.domain.providers.contracts import (
            AiBatchResultsInput,AiBatchStatusInput,AiBatchSubmissionInput,
        )
        seen=[]
        async def handler(request):
            seen.append((request.method,str(request.url)))
            if request.method=="GET" and str(request.url).endswith("batches?pageSize=100"):
                return httpx.Response(200,json={"operations":[]})
            if request.method=="POST" and "batchGenerateContent" in str(request.url):
                body=json.loads(request.content)
                self.assertEqual(body["batch"]["inputConfig"]["requests"]["requests"][0]["metadata"]["key"],"item-1")
                return httpx.Response(200,json={"name":"batches/1","state":"JOB_STATE_PENDING"})
            if request.method=="GET":
                return httpx.Response(200,json={
                    "name":"batches/1",
                    "response":{
                        "name":"batches/1","state":"BATCH_STATE_SUCCEEDED",
                        "output":{"inlinedResponses":{"inlinedResponses":[{
                            "metadata":{"key":"item-1"},
                            "response":{"candidates":[{"content":{"parts":[{"text":'{"subject":"cat"}'}]}}],
                                "usageMetadata":{"promptTokenCount":2},"responseId":"r1"}
                        }]}}
                    }})
            return httpx.Response(200,json={})
        provider=configured_gemini_provider(
            "secret",model="gemini-test",transport=httpx.MockTransport(handler))
        with tempfile.NamedTemporaryFile("w",delete=False) as handle:
            json.dump({"custom_item_id":"item-1","prompt":"analyze",
                "image_mime_type":"image/jpeg","image_base64":"YWJj",
                "metadata_profile":"general","metadata_profile_version":"1"},handle)
            handle.write("\n");path=handle.name
        try:
            submitted=await provider.submit_batch(AiBatchSubmissionInput(
                "tenant-a","key","display","gemini-test",path,1,100))
            self.assertEqual(submitted.provider_batch_id,"batches/1")
            status=await provider.get_batch_status(AiBatchStatusInput(
                "tenant-a","batches/1"))
            self.assertEqual(status.state,"completed")
            results=[value async for value in provider.stream_batch_results(
                AiBatchResultsInput("tenant-a","batches/1"))]
            self.assertEqual(results[0].custom_item_id,"item-1")
            self.assertEqual(results[0].result.metadata,{"subject":"cat"})
            self.assertTrue(await provider.cancel_batch(AiBatchStatusInput(
                "tenant-a","batches/1")))
        finally:
            Path(path).unlink(missing_ok=True)
