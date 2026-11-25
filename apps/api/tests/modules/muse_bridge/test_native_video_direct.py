"""Test exact native bytes at Drive/R2 boundaries; no multimedia binaries.

Fixtures exercise container headers only, not real video decoding or visual QA.
"""
import hashlib
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app.modules.muse_bridge import video_storage as s, ugc_media_cache as cache


def box(kind, payload):
    return (8+len(payload)).to_bytes(4,"big")+kind+payload


def fixture(ticks=6000):
    mvhd=bytes(12)+(1000).to_bytes(4,"big")+ticks.to_bytes(4,"big")+bytes(80)
    matrix=(65536).to_bytes(4,"big")+bytes(12)+(65536).to_bytes(4,"big")+bytes(16)
    tkhd=bytes(40)+matrix+(288*65536).to_bytes(4,"big")+(512*65536).to_bytes(4,"big")
    moov=box(b"mvhd",mvhd)+box(b"trak",box(b"tkhd",tkhd)+box(b"mdia",box(b"hdlr",bytes(8)+b"vide"+bytes(12))))
    # Deliberately moov AFTER mdat: no faststart remux is permitted.
    return box(b"ftyp",b"isom0000")+box(b"mdat",b"x"*4096)+box(b"moov",moov)


class HeaderTests(TestCase):
    def test_metadata_is_read_only(self):
        data=fixture()
        self.assertEqual(s.validate_mp4(data,"vertical"),{
            "mediaValidation":"native_container_only","durationSeconds":6.0,
            "width":288,"height":512})
        self.assertFalse(cache.has_faststart(data))

    def test_obvious_wrong_ratio_or_truncated_container(self):
        for data,aspect in ((fixture(),"horizontal"),(fixture()[:-1],"vertical"),(b"x"*4096,"vertical")):
            with self.assertRaises(HTTPException) as ctx:
                s.validate_mp4(data,aspect)
            self.assertEqual(ctx.exception.status_code,422)

    def test_optional_duration_not_fabricated(self):
        self.assertNotIn("durationSeconds",s.validate_mp4(fixture(0),"vertical"))


class StorageTests(IsolatedAsyncioTestCase):
    async def test_drive_and_r2_receive_identical_native_bytes(self):
        data=fixture(3000)
        sha=hashlib.sha256(data).hexdigest()
        async def save(arg):
            self.assertEqual(arg.content_hash,sha)
            self.assertEqual(arg.asset_id,"muse-ugc:123456789:"+"a"*64+":vertical")
            self.assertEqual(b"".join([part async for part in arg.body]),data)
            return SimpleNamespace(remote_file_id="native_file_123",
                                   remote_folder_id="native_folder_123")
        provider=SimpleNamespace(store_asset=AsyncMock(side_effect=save))
        folders={"video_folder_id":"native_folder_123","listing_folder_id":"listing_123",
                 "relative_path":"listing_123456789/design_"+("a"*16)+"_front/video_ugc"}
        provenance=AsyncMock()
        with patch.object(s,"provider_and_folder",return_value=(provider,s.ROOT_FOLDER_ID)), patch.object(s,"verify_private_drive_destination",AsyncMock()), patch.object(s,"ensure_listing_video_folder",AsyncMock(return_value=folders)), patch.object(s,"verify_listing_file_provenance",provenance), patch.object(cache,"config_for_tenant",return_value=None), patch.object(cache,"stage_uploaded",AsyncMock(return_value={"playback":True})) as stage:
            stored=await s.store_muse_video(tenant_id="test-tenant",listing_id="123456789",artwork_sha256="a"*64,aspect="vertical",mp4=data)
        provider.store_asset.assert_awaited_once()
        provenance.assert_awaited_once()
        self.assertEqual(stage.await_args.kwargs["mp4"],data)
        self.assertEqual(stage.await_args.kwargs["sha"],sha)
        self.assertTrue(stored["nativeBytesPreserved"])
        self.assertEqual(stored["sha256"],sha)
        self.assertEqual(stored["mediaProcessing"],"none")
        self.assertEqual(stored["qaStatus"],"pending_review")
        self.assertEqual(stored["durationSeconds"],3.0)

    async def test_historical_pinned_duration_kept(self):
        with patch.object(s,"provider_and_folder") as drive:
            with self.assertRaises(HTTPException) as ctx:
                await s.store_muse_video(tenant_id="test-tenant",listing_id="123456789",artwork_sha256="a"*64,aspect="vertical",mp4=fixture(),required_duration_seconds=(11.5,12.5))
            self.assertEqual(ctx.exception.status_code,422)
            drive.assert_not_called()
