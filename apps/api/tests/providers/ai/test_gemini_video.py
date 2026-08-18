from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from app.domain.providers.contracts import AiProviderError
from app.modules.video_search.proxy import PreparedVideoChunk
from app.providers.ai.gemini_video import MEDIA_RESOLUTION_HIGH, MEDIA_RESOLUTION_LOW, GeminiVideoClient


def _chunk(path: Path) -> PreparedVideoChunk:
    return PreparedVideoChunk(0, path, 120000, 125000, 5000, path.stat().st_size, 640, 360)


def _document() -> dict:
    return {"schema_version": "video-search-metadata-v1", "summary": "ok", "segments": []}


class GeminiVideoClientTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, handler, *, state="ACTIVE", clock=None, media_resolution=MEDIA_RESOLUTION_LOW):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chunk.mp4"; path.write_bytes(b"x" * (2 * 1024 * 1024 + 9))
            client = GeminiVideoClient("secret-video-key", model="gemini-test", transport=httpx.MockTransport(handler), poll_interval_seconds=0.1, processing_timeout_seconds=1, sleeper=lambda _: asyncio.sleep(0), monotonic=clock or (lambda: 0.0))
            return await client.analyze_proxy(chunk=_chunk(path), prompt="prompt", response_json_schema={"type": "object"}, media_resolution=media_resolution)

    async def test_upload_processing_active_generate_delete_and_streams_file(self):
        calls, upload_parts = [], []
        async def handler(request):
            calls.append((request.method, request.url.path))
            if request.url.path == "/upload/v1beta/files": return httpx.Response(200, headers={"X-Goog-Upload-URL": "https://upload.test/session"}, json={})
            if request.url.host == "upload.test":
                async for item in request.stream: upload_parts.append(item)
                return httpx.Response(200, json={"file": {"name": "files/x", "uri": "gemini://x", "state": "PROCESSING"}})
            if request.method == "GET": return httpx.Response(200, json={"name": "files/x", "uri": "gemini://x", "state": "ACTIVE"})
            if request.method == "POST":
                payload = json.loads((await request.aread()).decode())
                self.assertEqual(payload["contents"][0]["parts"][0]["file_data"]["file_uri"], "gemini://x")
                self.assertIn("responseJsonSchema", payload["generationConfig"])
                self.assertEqual(payload["generationConfig"]["mediaResolution"], MEDIA_RESOLUTION_LOW)
                return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(_document())}]}, "finishReason": "STOP"}], "modelVersion": "gemini-test", "responseId": "r", "usageMetadata": {}})
            return httpx.Response(204)
        result = await self._run(handler)
        self.assertEqual(result.document, _document()); self.assertEqual(sum(map(len, upload_parts)), 2 * 1024 * 1024 + 9)
        self.assertEqual(calls, [("POST", "/upload/v1beta/files"), ("POST", "/session"), ("GET", "/v1beta/files/x"), ("POST", "/v1beta/models/gemini-test:generateContent"), ("DELETE", "/v1beta/files/x")])

    async def test_detail_resolution_is_serialized(self):
        seen = []
        async def handler(request):
            if request.url.path == "/upload/v1beta/files": return httpx.Response(200, headers={"X-Goog-Upload-URL": "https://upload.test/session"}, json={})
            if request.url.host == "upload.test": return httpx.Response(200, json={"file":{"name":"files/x","uri":"gemini://x","state":"ACTIVE"}})
            if request.method == "POST":
                seen.append(json.loads((await request.aread()).decode())["generationConfig"]["mediaResolution"])
                return httpx.Response(200, json={"candidates":[{"content":{"parts":[{"text":json.dumps(_document())}]}}]})
            return httpx.Response(204)
        await self._run(handler, media_resolution=MEDIA_RESOLUTION_HIGH)
        self.assertEqual(seen, [MEDIA_RESOLUTION_HIGH])

    async def test_proxy_file_iterator_uses_multiple_bounded_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chunk.mp4"; path.write_bytes(b"x" * (2 * 1024 * 1024 + 9))
            blocks = [block async for block in GeminiVideoClient._file_chunks(path)]
        self.assertEqual([len(block) for block in blocks], [1024 * 1024, 1024 * 1024, 9])

    async def test_cancellation_after_upload_deletes_then_propagates(self):
        calls=[]
        async def handler(request):
            calls.append(request.method)
            if request.url.path == "/upload/v1beta/files": return httpx.Response(200, headers={"X-Goog-Upload-URL": "https://upload.test/session"}, json={})
            if request.url.host == "upload.test": return httpx.Response(200, json={"file":{"name":"files/x","uri":"gemini://x","state":"ACTIVE"}})
            if request.method == "POST": raise asyncio.CancelledError()
            return httpx.Response(204)
        with self.assertRaises(asyncio.CancelledError): await self._run(handler)
        self.assertEqual(calls[-1], "DELETE")

    async def test_active_skips_poll_and_delete_failure_preserves_success(self):
        calls=[]
        async def handler(request):
            calls.append((request.method, request.url.path))
            if request.url.path == "/upload/v1beta/files": return httpx.Response(200, headers={"X-Goog-Upload-URL": "https://upload.test/session"}, json={})
            if request.url.host == "upload.test": return httpx.Response(200, json={"file": {"name": "files/x", "uri": "gemini://x", "state": "ACTIVE"}})
            if request.method == "POST": return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(_document())}]}}]})
            return httpx.Response(500, json={"error": {}})
        result = await self._run(handler)
        self.assertFalse(result.provider_metadata["temporary_file_deleted"]); self.assertNotIn(("GET", "/v1beta/files/x"), calls)

    async def test_processing_timeout_and_failed_delete_without_generation(self):
        for state, code in (("PROCESSING", "gemini_video_processing_timeout"), ("FAILED", "gemini_video_processing_failed")):
            calls=[]; ticks=iter((0.0, 2.0))
            async def handler(request, state=state):
                calls.append(request.method)
                if request.url.path == "/upload/v1beta/files": return httpx.Response(200, headers={"X-Goog-Upload-URL": "https://upload.test/session"}, json={})
                if request.url.host == "upload.test": return httpx.Response(200, json={"file": {"name":"files/x","uri":"gemini://x","state":state}})
                return httpx.Response(204)
            with self.assertRaises(AiProviderError) as raised: await self._run(handler, clock=lambda: next(ticks))
            self.assertEqual(raised.exception.code, code); self.assertEqual(calls[-1], "DELETE"); self.assertNotIn("POST", calls[2:])

    async def test_generation_invalid_json_and_error_cleanup_and_key_never_exposed(self):
        for response, code in ((httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{"}]}}]}), "gemini_video_invalid_json"), (httpx.Response(500, json={"error": {"message": "secret-video-key"}}), "gemini_video_http_error")):
            calls=[]
            async def handler(request, response=response):
                calls.append(request.method)
                if request.url.path == "/upload/v1beta/files": return httpx.Response(200, headers={"X-Goog-Upload-URL": "https://upload.test/session"}, json={})
                if request.url.host == "upload.test": return httpx.Response(200, json={"file":{"name":"files/x","uri":"gemini://x","state":"ACTIVE"}})
                if request.method == "POST": return response
                return httpx.Response(204)
            with self.assertRaises(AiProviderError) as raised: await self._run(handler)
            self.assertEqual(raised.exception.code, code); self.assertNotIn("secret-video-key", str(raised.exception)); self.assertEqual(calls[-1], "DELETE")
