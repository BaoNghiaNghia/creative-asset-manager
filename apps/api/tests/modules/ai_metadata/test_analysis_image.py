import io
import tempfile
import unittest

from PIL import Image

from app.domain.providers.contracts import (
    OpenStoredAssetInput,
    StorageProviderError,
    StoredAssetReadStream,
)
from app.modules.ai_metadata.analysis_image import (
    AnalysisImageError,
    AnalysisImageLimits,
    AnalysisImagePreparer,
)


class FakeStorage:
    def __init__(self, content):
        self.content = content
        self.closed = False

    async def open_asset(self, _input):
        async def body():
            yield self.content[:10]
            yield self.content[10:]
        async def close():
            self.closed = True
        return StoredAssetReadStream(body=body(), close=close, content_type="image/png")

    async def store_asset(self, _input):
        raise NotImplementedError

    async def store_metadata_sidecar(self, _input):
        raise NotImplementedError

class ErrorStorage(FakeStorage):
    def __init__(self, error):
        super().__init__(b"")
        self.error = error

    async def open_asset(self, _input):
        raise self.error



class AnalysisImagePreparerTest(unittest.IsolatedAsyncioTestCase):
    async def test_orients_strips_metadata_hashes_and_cleans_temp_files(self):
        source = io.BytesIO()
        Image.new("RGBA", (30, 20), (255, 0, 0, 128)).save(
            source, format="PNG", pnginfo=None
        )
        storage = FakeStorage(source.getvalue())
        with tempfile.TemporaryDirectory() as temp_dir:
            result = await AnalysisImagePreparer(
                storage, temp_dir=temp_dir
            ).prepare(
                OpenStoredAssetInput(
                    tenant_id="tenant-a", asset_id="asset-a", remote_file_id="file-a"
                )
            )
            import os
            self.assertEqual(os.listdir(temp_dir), [])
        self.assertTrue(storage.closed)
        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertEqual(len(result.content_hash), 64)
        with Image.open(io.BytesIO(result.content)) as prepared:
            self.assertEqual(prepared.size, (30, 20))
            self.assertFalse(prepared.getexif())

    async def test_large_source_is_resized_before_final_pixel_validation(self):
        source = io.BytesIO()
        Image.new("RGB", (6000, 5000), (255, 255, 255)).save(
            source, format="JPEG", quality=40
        )
        result = await AnalysisImagePreparer(FakeStorage(source.getvalue())).prepare(
            OpenStoredAssetInput(
                tenant_id="tenant-a", asset_id="asset-a", remote_file_id="file-a"
            )
        )
        with Image.open(io.BytesIO(result.content)) as prepared:
            self.assertLessEqual(prepared.width, 4096)
            self.assertLessEqual(prepared.height, 4096)
            self.assertLessEqual(prepared.width * prepared.height, 24_000_000)

    async def test_source_above_decode_pixel_limit_is_rejected(self):
        source = io.BytesIO()
        Image.new("RGB", (20, 20), (255, 255, 255)).save(source, format="PNG")
        preparer = AnalysisImagePreparer(
            FakeStorage(source.getvalue()),
            limits=AnalysisImageLimits(max_decode_pixels=100),
        )
        with self.assertRaises(AnalysisImageError) as raised:
            await preparer.prepare(
                OpenStoredAssetInput(
                    tenant_id="tenant-a", asset_id="asset-a", remote_file_id="file-a"
                )
            )
        self.assertEqual(raised.exception.code, "analysis_image_dimensions")
        self.assertFalse(raised.exception.retryable)

    async def test_storage_errors_preserve_safe_classification(self):
        cases = (
            ("managed_storage_object_missing", False, "analysis_storage_object_missing"),
            ("managed_storage_forbidden", False, "analysis_storage_access_denied"),
            (
                "managed_storage_temporarily_unavailable",
                True,
                "analysis_storage_temporarily_unavailable",
            ),
            (
                "managed_storage_network_error",
                True,
                "analysis_storage_temporarily_unavailable",
            ),
        )
        for provider_code, retryable, expected_code in cases:
            with self.subTest(provider_code=provider_code):
                preparer = AnalysisImagePreparer(
                    ErrorStorage(
                        StorageProviderError(
                            "safe provider failure",
                            code=provider_code,
                            retryable=retryable,
                        )
                    )
                )
                with self.assertRaises(AnalysisImageError) as raised:
                    await preparer.prepare(
                        OpenStoredAssetInput(
                            tenant_id="tenant-a",
                            asset_id="asset-a",
                            remote_file_id="file-a",
                        )
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(raised.exception.retryable, retryable)

