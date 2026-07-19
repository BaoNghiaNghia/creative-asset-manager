import unittest

from app.domain.providers.contracts import ListSourceChangesInput, SourceChangePage
from app.providers.google.source_adapter import GoogleDriveSourceAdapter
from app.providers.microsoft.source_adapter import SharePointSourceAdapter


class IncrementalAdapterContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_google_adapter_forwards_changes_cursor(self) -> None:
        calls = []

        async def lister(token, input):
            calls.append((token, input))
            return SourceChangePage((), "next-google", False)

        adapter = GoogleDriveSourceAdapter("secret", changes_lister=lister)
        page = await adapter.list_changes(
            ListSourceChangesInput("source-1", cursor="google-cursor")
        )
        self.assertEqual(page.next_cursor, "next-google")
        self.assertEqual(calls[0][1].cursor, "google-cursor")

    async def test_sharepoint_adapter_forwards_delta_cursor_and_metadata(self) -> None:
        calls = []

        async def lister(token, input):
            calls.append((token, input))
            return SourceChangePage((), "next-delta", False)

        adapter = SharePointSourceAdapter("secret", changes_lister=lister)
        page = await adapter.list_changes(
            ListSourceChangesInput(
                "source-1",
                cursor="https://graph.microsoft.com/delta-token",
                source_metadata={"drive_id": "drive-1"},
            )
        )
        self.assertEqual(page.next_cursor, "next-delta")
        self.assertEqual(calls[0][1].source_metadata["drive_id"], "drive-1")
