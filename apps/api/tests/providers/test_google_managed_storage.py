import unittest

import httpx

from app.domain.providers.contracts import (
    StorageProviderError,
    StoreAssetInput,
    StoreMetadataSidecarInput,
)
from app.providers.google.storage import GoogleDriveAssetStorage


async def body(value: bytes):
    yield value


class GoogleDriveAssetStorageTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_remote_asset_is_returned_without_upload(self) -> None:
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "remote-1",
                            "parents": ["managed-root"],
                            "webViewLink": "https://drive.google.com/file/remote-1",
                            "size": "3",
                        }
                    ]
                },
            )

        provider = GoogleDriveAssetStorage(
            "storage-token",
            root_folder_id="managed-root",
            transport=httpx.MockTransport(handler),
        )
        result = await provider.store_asset(
            StoreAssetInput(
                tenant_id="tenant-a",
                asset_id="asset-1",
                content_hash="a" * 64,
                body=body(b"new"),
                filename="creative.png",
            )
        )
        self.assertEqual(result.remote_file_id, "remote-1")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "GET")
        self.assertNotIn("storage-token", str(requests[0].url))

    async def test_retry_finds_uploaded_file_and_does_not_duplicate(self) -> None:
        remote = None
        upload_count = 0
        uploaded_body = b""

        async def handler(request):
            nonlocal remote, upload_count, uploaded_body
            if request.method == "GET":
                return httpx.Response(200, json={"files": [remote] if remote else []})
            if request.method == "POST":
                upload_count += 1
                metadata = __import__("json").loads((await request.aread()).decode())
                self.assertEqual(metadata["name"], f"{'b' * 64}.png")
                self.assertEqual(metadata["parents"], ["managed-root"])
                self.assertEqual(metadata["appProperties"]["cam_asset_id"], "asset-1")
                return httpx.Response(
                    200,
                    headers={"location": "https://www.googleapis.com/upload/session-1"},
                )
            uploaded_body = await request.aread()
            remote = {
                "id": "remote-1",
                "parents": ["managed-root"],
                "webViewLink": "https://drive.google.com/file/remote-1",
                "size": str(len(uploaded_body)),
            }
            return httpx.Response(200, json=remote)

        provider = GoogleDriveAssetStorage(
            "storage-token",
            root_folder_id="managed-root",
            transport=httpx.MockTransport(handler),
        )
        first = await provider.store_asset(
            StoreAssetInput(
                tenant_id="tenant-a",
                asset_id="asset-1",
                content_hash="b" * 64,
                body=body(b"abc"),
                content_type="image/png",
                size_bytes=3,
                filename="creative.png",
            )
        )
        second = await provider.store_asset(
            StoreAssetInput(
                tenant_id="tenant-a",
                asset_id="asset-1",
                content_hash="b" * 64,
                body=body(b"must-not-upload"),
                filename="renamed.png",
            )
        )
        self.assertEqual(first.remote_file_id, second.remote_file_id)
        self.assertEqual(uploaded_body, b"abc")
        self.assertEqual(upload_count, 1)

    async def test_retryable_google_failure_is_classified(self) -> None:
        provider = GoogleDriveAssetStorage(
            "storage-token",
            root_folder_id="managed-root",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, text="unavailable")
            ),
        )
        with self.assertRaises(StorageProviderError) as context:
            await provider.store_asset(
                StoreAssetInput(
                    tenant_id="tenant-a",
                    asset_id="asset-1",
                    content_hash="c" * 64,
                    body=body(b"abc"),
                )
            )
        self.assertTrue(context.exception.retryable)

    async def test_metadata_sidecar_create_then_update_reuses_remote_file(self) -> None:
        remote = None
        methods = []
        uploaded_documents = []

        async def handler(request):
            nonlocal remote
            methods.append(request.method)
            if request.method == "GET":
                return httpx.Response(200, json={"files": [remote] if remote else []})
            if request.method == "POST":
                metadata = __import__("json").loads((await request.aread()).decode())
                self.assertEqual(metadata["appProperties"]["cam_analysis_id"], "analysis-1")
                return httpx.Response(
                    200,
                    headers={"location": "https://www.googleapis.com/upload/sidecar-1"},
                )
            uploaded_documents.append(
                __import__("json").loads((await request.aread()).decode())
            )
            remote = {
                "id": "sidecar-remote-1",
                "parents": ["managed-root"],
                "webViewLink": "https://drive.google.com/file/sidecar-remote-1",
            }
            return httpx.Response(200, json=remote)

        provider = GoogleDriveAssetStorage(
            "storage-token",
            root_folder_id="managed-root",
            transport=httpx.MockTransport(handler),
        )
        first = await provider.store_metadata_sidecar(
            StoreMetadataSidecarInput(
                tenant_id="tenant-a",
                asset_id="asset-1",
                analysis_id="analysis-1",
                metadata={"asset": {"asset_id": "asset-1"}, "version": 1},
                document_hash="a" * 64,
            )
        )
        second = await provider.store_metadata_sidecar(
            StoreMetadataSidecarInput(
                tenant_id="tenant-a",
                asset_id="asset-1",
                analysis_id="analysis-1",
                metadata={"asset": {"asset_id": "asset-1"}, "version": 2},
                document_hash="b" * 64,
            )
        )
        self.assertEqual(first.remote_file_id, second.remote_file_id)
        self.assertEqual(methods.count("POST"), 1)
        self.assertEqual(methods.count("PATCH"), 1)
        self.assertEqual(uploaded_documents, [
            {"asset": {"asset_id": "asset-1"}, "version": 1},
            {"asset": {"asset_id": "asset-1"}, "version": 2},
        ])

    async def test_refresh_token_is_preferred_cached_and_refreshed_before_expiry(
        self,
    ) -> None:
        clock = [100.0]
        token_requests = []

        async def handler(request):
            token_requests.append(request)
            payload = (await request.aread()).decode()
            self.assertIn("grant_type=refresh_token", payload)
            self.assertIn("refresh_token=refresh-secret", payload)
            return httpx.Response(
                200,
                json={
                    "access_token": f"refreshed-{len(token_requests)}",
                    "expires_in": 120,
                },
            )

        provider = GoogleDriveAssetStorage(
            "fallback-token",
            root_folder_id="managed-root",
            refresh_token="refresh-secret",
            client_id="client-id",
            client_secret="client-secret",
            transport=httpx.MockTransport(handler),
            clock=lambda: clock[0],
        )

        self.assertEqual(await provider._get_access_token(), "refreshed-1")
        self.assertEqual(await provider._get_access_token(), "refreshed-1")
        self.assertEqual(len(token_requests), 1)
        self.assertEqual(
            str(token_requests[0].url),
            "https://oauth2.googleapis.com/token",
        )

        clock[0] = 161.0
        self.assertEqual(await provider._get_access_token(), "refreshed-2")
        self.assertEqual(len(token_requests), 2)

    async def test_delete_uses_managed_remote_identity_and_supports_all_drives(self) -> None:
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(204)

        provider = GoogleDriveAssetStorage(
            "storage-token", root_folder_id="managed-root",
            transport=httpx.MockTransport(handler),
        )
        from app.domain.providers.contracts import DeleteStoredAssetInput
        await provider.delete_asset(DeleteStoredAssetInput(
            tenant_id="tenant-a", asset_id="internal-asset", remote_file_id="managed-remote-id",
        ))
        self.assertEqual(requests[0].method, "DELETE")
        self.assertIn("managed-remote-id", str(requests[0].url))
        self.assertEqual(requests[0].url.params["supportsAllDrives"], "true")

    async def test_delete_404_is_reported_as_missing_and_429_is_retryable(self) -> None:
        from app.domain.providers.contracts import DeleteStoredAssetInput
        input = DeleteStoredAssetInput("tenant-a", "asset-a", "managed-id")
        for code, expected, retryable in ((404, "managed_storage_object_missing", False), (429, "managed_storage_temporarily_unavailable", True)):
            provider = GoogleDriveAssetStorage(
                "storage-token", root_folder_id="managed-root",
                transport=httpx.MockTransport(lambda _request, status=code: httpx.Response(status)),
            )
            with self.assertRaises(StorageProviderError) as context:
                await provider.delete_asset(input)
            self.assertEqual(context.exception.code, expected)
            self.assertEqual(context.exception.retryable, retryable)
