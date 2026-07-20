import io
import tempfile
import unittest

from PIL import Image

from app.domain.providers.contracts import StoredAssetReadStream, OpenStoredAssetInput
from app.modules.ai_metadata.analysis_image import AnalysisImagePreparer


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
