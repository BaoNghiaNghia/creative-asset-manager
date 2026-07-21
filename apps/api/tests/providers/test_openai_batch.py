import base64
import json
import os
import tempfile
import unittest

import httpx
import openai
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.domain.providers.contracts import (
    AiBatchResultsInput,
    AiBatchStatusInput,
    AiBatchSubmissionInput,
    AiProviderError,
)
from app.providers.ai.openai import OpenAiMetadataProvider


class _StreamingResponse:
    def __init__(self, lines):
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def iter_lines(self):
        for line in self.lines:
            yield line


class FakeOpenAiBatchClient:
    def __init__(self):
        self.uploaded = None
        self.upload_path = None
        self.batch = SimpleNamespace(
            id="batch_1", _request_id="req_batch_1",
            status="validating", input_file_id="file_input",
            output_file_id=None, error_file_id=None,
            metadata={"cam_submission_key": "submission-1"})
        self.list_values = []
        self.output_lines = {}
        self.files = SimpleNamespace()
        self.files.create = AsyncMock(side_effect=self._upload)
        self.files.with_streaming_response = SimpleNamespace(
            content=lambda file_id, **_kwargs: _StreamingResponse(
                self.output_lines.get(file_id, [])))
        self.batches = SimpleNamespace(
            list=AsyncMock(side_effect=self._list),
            create=AsyncMock(side_effect=self._create),
            retrieve=AsyncMock(side_effect=self._retrieve),
            cancel=AsyncMock(return_value=SimpleNamespace(status="cancelling")))
        self.close = AsyncMock()

    async def _upload(self, *, file, **_kwargs):
        self.upload_path = str(file)
        self.uploaded = Path(file).read_text(encoding="utf-8")
        return SimpleNamespace(id="file_input")

    async def _list(self, **_kwargs):
        return SimpleNamespace(data=list(self.list_values))

    async def _create(self, **_kwargs):
        self.create_kwargs = _kwargs
        return self.batch

    async def _retrieve(self, _batch_id, **_kwargs):
        return self.batch


def provider(client, **overrides):
    values = dict(
        api_key="sk-test", model="openai-test",
        allowed_models=("openai-test",), client=client,
        batch_enabled=True, batch_max_items=10,
        batch_max_file_bytes=2_000_000,
        batch_input_retention_hours=24,
        batch_output_retention_hours=24)
    values.update(overrides)
    return OpenAiMetadataProvider(**values)


def neutral_input(custom_id="item-1"):
    row = {
        "custom_item_id": custom_id,
        "prompt": "Extract metadata",
        "image_mime_type": "image/png",
        "image_base64": base64.b64encode(b"bounded-image").decode("ascii"),
        "metadata_profile": "general",
        "metadata_profile_version": "1",
        "json_schema": {
            "type": "object",
            "properties": {"subject": {"type": "string"}},
            "required": ["subject"],
        },
    }
    handle = tempfile.NamedTemporaryFile(
        suffix=".jsonl", mode="w", delete=False, encoding="utf-8")
    with handle:
        handle.write(json.dumps(row) + "\n")
    return handle.name


def submission_input(path):
    return AiBatchSubmissionInput(
        tenant_id="tenant-a", submission_key="submission-1",
        display_name="cam-batch-a", model="openai-test",
        input_path=path, item_count=1,
        total_bytes=os.path.getsize(path))


class OpenAiBatchProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_transforms_uploads_and_submits_responses_jsonl(self):
        client = FakeOpenAiBatchClient()
        path = neutral_input()
        try:
            result = await provider(client).submit_batch(submission_input(path))
        finally:
            Path(path).unlink(missing_ok=True)

        self.assertEqual(result.provider_batch_id, "batch_1")
        self.assertEqual(result.provider_metadata["input_file_id"], "file_input")
        request = json.loads(client.uploaded)
        self.assertEqual(request["custom_id"], "item-1")
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], "/v1/responses")
        self.assertEqual(request["body"]["model"], "openai-test")
        self.assertTrue(request["body"]["text"]["format"]["strict"])
        self.assertTrue(
            request["body"]["input"][0]["content"][1]["image_url"].startswith(
                "data:image/png;base64,"))
        self.assertEqual(client.create_kwargs["endpoint"], "/v1/responses")
        self.assertEqual(client.create_kwargs["completion_window"], "24h")
        self.assertFalse(os.path.exists(client.upload_path))

    async def test_reconciles_existing_submission_without_upload(self):
        client = FakeOpenAiBatchClient()
        client.list_values = [client.batch]
        path = neutral_input()
        try:
            result = await provider(client).submit_batch(submission_input(path))
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(result.provider_batch_id, "batch_1")
        client.files.create.assert_not_awaited()
        client.batches.create.assert_not_awaited()

    async def test_ambiguous_create_does_not_retry_blindly(self):
        client = FakeOpenAiBatchClient()
        calls = 0

        async def listed(**_kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(data=[] if calls == 1 else [client.batch])

        client.batches.list.side_effect = listed
        client.batches.create.side_effect = TimeoutError("response lost")
        path = neutral_input()
        try:
            result = await provider(client).submit_batch(submission_input(path))
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(result.provider_batch_id, "batch_1")
        self.assertEqual(client.batches.create.await_count, 1)

    async def test_definite_submit_authentication_error_is_not_ambiguous(self):
        client = FakeOpenAiBatchClient()
        request = httpx.Request(
            "POST", "https://api.openai.com/v1/batches")
        client.batches.create.side_effect = openai.AuthenticationError(
            "secret body",
            response=httpx.Response(401, request=request),
            body={"error": {"message": "secret body"}})
        path = neutral_input()
        try:
            with self.assertRaises(AiProviderError) as raised:
                await provider(client).submit_batch(submission_input(path))
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(
            raised.exception.code, "openai_authentication_failed")
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn("secret body", str(raised.exception))
        self.assertEqual(client.batches.list.await_count, 1)
    async def test_status_maps_all_states_and_expired_partial_is_importable(self):
        client = FakeOpenAiBatchClient()
        instance = provider(client)
        expected = {
            "validating": "pending", "in_progress": "running",
            "finalizing": "running", "completed": "completed",
            "failed": "failed", "expired": "expired",
            "cancelling": "running", "cancelled": "cancelled"}
        for source, target in expected.items():
            client.batch.status = source
            client.batch.output_file_id = None
            client.batch.error_file_id = None
            status = await instance.get_batch_status(
                AiBatchStatusInput("tenant-a", "batch_1"))
            self.assertEqual(status.state, target)
        client.batch.status = "expired"
        client.batch.output_file_id = "file_output"
        status = await instance.get_batch_status(
            AiBatchStatusInput("tenant-a", "batch_1"))
        self.assertEqual(status.state, "completed")

    async def test_streams_out_of_order_success_and_error_results(self):
        client = FakeOpenAiBatchClient()
        client.batch.status = "completed"
        client.batch.output_file_id = "file_output"
        client.batch.error_file_id = "file_error"
        success = {
            "id": "line-2", "custom_id": "item-2",
            "response": {"status_code": 200, "request_id": "req-2", "body": {
                "id": "resp-2", "status": "completed", "model": "openai-test",
                "output": [{"content": [{"type": "output_text",
                                        "text": "{\"subject\":\"cat\"}"}]}],
                "usage": {"input_tokens": 3, "output_tokens": 2}}}}
        failure = {
            "id": "line-1", "custom_id": "item-1",
            "error": {"code": "rate_limit_exceeded", "message": "limited"}}
        client.output_lines = {
            "file_output": [json.dumps(success)],
            "file_error": [json.dumps(failure)]}
        instance = provider(client)
        results = [value async for value in instance.stream_batch_results(
            AiBatchResultsInput("tenant-a", "batch_1"))]
        self.assertEqual([value.custom_item_id for value in results],
                         ["item-2", "item-1"])
        self.assertEqual(results[0].result.metadata, {"subject": "cat"})
        self.assertEqual(results[0].result.usage["input_tokens"], 3)
        self.assertTrue(results[1].retryable)
        self.assertEqual(results[1].error_code,
                         "openai_batch_rate_limit_exceeded")

    async def test_rejects_duplicate_custom_ids_and_disabled_batch(self):
        client = FakeOpenAiBatchClient()
        path = neutral_input()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(Path(path).read_text(encoding="utf-8"))
        try:
            with self.assertRaises(AiProviderError) as raised:
                await provider(client).submit_batch(
                    AiBatchSubmissionInput(
                        "tenant-a", "submission-1", "batch", "openai-test",
                        path, 2, os.path.getsize(path)))
            self.assertEqual(raised.exception.code,
                             "openai_batch_invalid_custom_id")
        finally:
            Path(path).unlink(missing_ok=True)

        disabled = provider(client, batch_enabled=False)
        with self.assertRaises(AiProviderError) as raised:
            await disabled.get_batch_status(
                AiBatchStatusInput("tenant-a", "batch_1"))
        self.assertEqual(raised.exception.code, "openai_batch_disabled")

    async def test_cancel_is_idempotent_for_terminal_batch(self):
        client = FakeOpenAiBatchClient()
        client.batch.status = "completed"
        self.assertTrue(await provider(client).cancel_batch(
            AiBatchStatusInput("tenant-a", "batch_1")))
        client.batches.cancel.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
