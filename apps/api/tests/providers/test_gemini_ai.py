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

        provider=GeminiAiMetadataProvider(
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
        provider=GeminiAiMetadataProvider(
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
