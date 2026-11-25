"""Original bytes only, including actual response hash and no thumbnail fallback."""
import hashlib
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from PIL import Image
from app.modules.muse_bridge import original_artwork as a, router as m, observability as obs

LID = "123456789"
JOB = "12345678-1234-1234-1234-123456789abc"
ASSET = "a" * 24
TENANT = "test-tenant"

def png(size):
    out = io.BytesIO()
    Image.new("RGBA", size, (120, 30, 60, 100)).save(out, format="PNG")
    return out.getvalue()

ORIGINAL = png((1536, 2048))
THUMBNAIL = png((192, 192))
SHA = hashlib.sha256(ORIGINAL).hexdigest()

class DB:
    def __init__(self, row=None):
        self.row = row
        self.rollbacks = 0
    def get(self, *_):
        return self.row
    def rollback(self):
        self.rollbacks += 1

class OriginalPolicyTests(TestCase):
    def test_response_bytes_exact(self):
        res = a.verified_response(ORIGINAL, SHA, "artwork.png")
        self.assertEqual(res.body, ORIGINAL)
        self.assertEqual(res.media_type, "image/png")
        self.assertEqual(res.headers["X-CAM-Artwork-SHA256"], SHA)
        self.assertEqual(res.headers["X-CAM-Artwork-Variant"], "original")
        self.assertEqual(res.headers["Cache-Control"], "private, no-store")

    def test_thumbnail_cannot_replace_original(self):
        with self.assertRaises(HTTPException) as ctx:
            a.verified_response(THUMBNAIL, SHA, "artwork.png")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_non_png_rejected_even_if_digest_matches(self):
        raw = b"X" * 100
        with self.assertRaises(HTTPException):
            a.verified_response(raw, hashlib.sha256(raw).hexdigest(), "artwork.png")

    def test_policy_and_compact_keep_source(self):
        src = a.source(LID, SHA)
        self.assertEqual(src["assetVariant"], "original")
        self.assertEqual(src["expectedSha256"], SHA)
        self.assertFalse(src["thumbnailFallbackAllowed"])
        self.assertTrue(src["requiresClaimedVideoJob"])
        self.assertEqual(src["path"], f"/api/v1/muse-bridge/jobs/{LID}/artwork")
        review = a.source(LID, SHA, review=True)
        self.assertEqual(review["path"], f"/api/v1/muse-bridge/reviews/{LID}/artwork")
        self.assertIsNone(a.source(LID, None))
        self.assertEqual(m._compact_discovery_item({"listingId": LID, "artworkSource": src})["artworkSource"], src)
        self.assertFalse(a.policy()["thumbnailAllowed"])
        self.assertFalse(a.policy()["previewAllowed"])

    def test_telemetry_only_bounded_original_metadata(self):
        self.assertEqual(obs.safe_business_result({"artworkOriginalVerified": True, "artworkBytes": len(ORIGINAL)}),
                         {"artworkOriginalVerified": True, "artworkBytes": len(ORIGINAL)})
        self.assertNotIn("artworkBytes", obs.safe_business_result({"artworkOriginalVerified": True, "artworkBytes": a.MAX_BYTES + 1}))

class OriginalReadTests(IsolatedAsyncioTestCase):
    async def test_drive_reads_original_id_and_sha_not_thumbnail(self):
        db = DB()
        remote = {"output_sha256": SHA, "drive_file_id": "original-file", "drive_thumbnail_file_id": "thumbnail-file"}
        read = AsyncMock(return_value=ORIGINAL)
        with patch.object(m, "_private_drive_design", return_value=remote), patch("app.modules.etsy_spy.imagegen_drive.read_design_from_drive", read), patch.object(m, "_checked_design_media") as local:
            res = await m._read_original_artwork(db, TENANT, JOB, ASSET, SHA, "artwork.png")
        read.assert_awaited_once_with(tenant_id=TENANT, asset_id=JOB+":"+ASSET+":original", remote_id="original-file", sha256=SHA, max_bytes=a.MAX_BYTES)
        local.assert_not_called()
        self.assertEqual(res.body, ORIGINAL)
        self.assertEqual(db.rollbacks, 1)

    async def test_bad_remote_no_local_or_thumbnail_fallback(self):
        read = AsyncMock(return_value=THUMBNAIL)
        with patch.object(m, "_private_drive_design", return_value={"output_sha256": SHA, "drive_file_id": "wrong"}), patch("app.modules.etsy_spy.imagegen_drive.read_design_from_drive", read), patch.object(m, "_checked_design_media") as local:
            with self.assertRaises(HTTPException) as ctx:
                await m._read_original_artwork(DB(), TENANT, JOB, ASSET, SHA, "artwork.png")
            self.assertEqual(ctx.exception.status_code, 409)
        local.assert_not_called()

    async def test_local_bytes_are_reverified_after_metadata_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "master.png"
            path.write_bytes(ORIGINAL)
            with patch.object(m, "_private_drive_design", return_value=None), patch.object(m, "_checked_design_media", return_value=(path, SHA)):
                res = await m._read_original_artwork(DB(), TENANT, JOB, ASSET, SHA, "artwork.png")
                self.assertEqual(res.body, ORIGINAL)
                path.write_bytes(THUMBNAIL)
                with self.assertRaises(HTTPException) as ctx:
                    await m._read_original_artwork(DB(), TENANT, JOB, ASSET, SHA, "artwork.png")
                self.assertEqual(ctx.exception.status_code, 409)

    async def test_metadata_mismatch_rejected_before_drive_download(self):
        read = AsyncMock()
        with patch.object(m, "_private_drive_design", return_value={"output_sha256": "0"*64, "drive_file_id": "wrong"}), patch("app.modules.etsy_spy.imagegen_drive.read_design_from_drive", read):
            with self.assertRaises(HTTPException) as ctx:
                await m._read_original_artwork(DB(), TENANT, JOB, ASSET, SHA, "artwork.png")
            self.assertEqual(ctx.exception.status_code, 409)
        read.assert_not_awaited()
