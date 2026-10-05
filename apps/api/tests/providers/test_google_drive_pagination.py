import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.providers.google.drive import GoogleDriveClient


class GoogleDrivePaginationTest(unittest.IsolatedAsyncioTestCase):
    async def _client_with_pages(self, pages: list[dict]):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=pages.pop(0))

        client = GoogleDriveClient("test-token")
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://www.googleapis.com/drive/v3",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.client.aclose)
        return client, requests

    @staticmethod
    def _file(item_id: str) -> dict:
        return {
            "id": item_id,
            "name": f"{item_id}.png",
            "mimeType": "image/png",
            "parents": ["folder-1"],
        }

    async def test_search_folders_uses_bounded_drive_name_query(self) -> None:
        folder = {
            "id": "folder-asin",
            "name": "4347749385",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["amazon-root"],
        }
        client, requests = await self._client_with_pages([{"files": [folder]}])

        nodes = await client.search_folders(" 4347749385 ", limit=999)

        self.assertEqual([(node.id, node.name, node.kind) for node in nodes], [
            ("folder-asin", "4347749385", "folder"),
        ])
        self.assertEqual(len(requests), 1)
        params = requests[0].url.params
        self.assertEqual(params["pageSize"], "100")
        self.assertEqual(params["orderBy"], "name")
        self.assertIn("name contains '4347749385'", params["q"])
        self.assertIn("mimeType = 'application/vnd.google-apps.folder'", params["q"])

    async def test_children_page_makes_one_bounded_request(self) -> None:
        client, requests = await self._client_with_pages([
            {"files": [self._file("one")], "nextPageToken": "next-page"},
        ])

        nodes, next_token = await client.children_page(
            "folder-1", page_token="opaque-token", page_size=999
        )

        self.assertEqual([node.id for node in nodes], ["one"])
        self.assertEqual(next_token, "next-page")
        self.assertEqual(len(requests), 1)
        params = requests[0].url.params
        self.assertEqual(params["pageToken"], "opaque-token")
        self.assertEqual(params["pageSize"], "200")
        self.assertEqual(params["orderBy"], "folder,name")
        self.assertIn("'folder-1' in parents", params["q"])

    async def test_children_keeps_full_iteration_for_tree_and_background_callers(self) -> None:
        client, requests = await self._client_with_pages([
            {"files": [self._file("one")], "nextPageToken": "page-2"},
            {"files": [self._file("two")]},
        ])

        nodes = await client.children("folder-1")

        self.assertEqual([node.id for node in nodes], ["one", "two"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url.params["pageSize"], "200")
        self.assertEqual(requests[1].url.params["pageToken"], "page-2")

    async def test_get_retries_transient_read_timeout(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("temporary Drive timeout", request=request)
            return httpx.Response(200, json={"files": [self._file("one")]})

        client = GoogleDriveClient("test-token")
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://www.googleapis.com/drive/v3",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.client.aclose)

        with patch(
            "app.providers.google.drive.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            data = await client._get("/files", {"pageSize": "1"})

        self.assertEqual(calls, 2)
        self.assertEqual(data["files"][0]["id"], "one")
        sleep.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
