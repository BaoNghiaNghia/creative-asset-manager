import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.providers.google.drive import open_media_stream


class GoogleDriveMediaStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_follows_redirects_for_authenticated_media(self):
        response = MagicMock(status_code=200, headers={})
        response.raise_for_status = MagicMock()
        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=response)

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client) as build:
            returned_client, returned_response = await open_media_stream("token", "file-id", None)

        self.assertIs(returned_client, client)
        self.assertIs(returned_response, response)
        self.assertTrue(build.call_args.kwargs["follow_redirects"])

    async def test_retries_transient_google_failure_before_streaming(self):
        transient = MagicMock(status_code=503, headers={"retry-after": "0"})
        transient.aclose = AsyncMock()
        success = MagicMock(status_code=200, headers={})
        success.raise_for_status = MagicMock()
        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(side_effect=(transient, success))

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client), patch(
            "app.providers.google.drive.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            _, response = await open_media_stream("token", "file-id", "bytes=0-")

        self.assertIs(response, success)
        self.assertEqual(client.send.await_count, 2)
        transient.aclose.assert_awaited_once()
        sleep.assert_awaited_once_with(0.0)

    async def test_non_retryable_provider_error_closes_resources(self):
        response = httpx.Response(403, request=httpx.Request("GET", "https://example.test"))
        response.aclose = AsyncMock()
        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=response)
        client.aclose = AsyncMock()

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client):
            with self.assertRaises(httpx.HTTPStatusError):
                await open_media_stream("token", "file-id", None)

        response.aclose.assert_awaited_once()
        client.aclose.assert_awaited_once()
