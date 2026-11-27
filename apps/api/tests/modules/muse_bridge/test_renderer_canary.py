"""Offline boundary checks: fixtures are not renderer evidence."""
import hashlib
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app.modules.muse_bridge import renderer_canary as c

def box(kind, payload):
    return (8+len(payload)).to_bytes(4,"big")+kind+payload

def fixture(ticks=10000, width=720, height=1280):
    mvhd=bytes(12)+(1000).to_bytes(4,"big")+ticks.to_bytes(4,"big")+bytes(80)
    matrix=(65536).to_bytes(4,"big")+bytes(12)+(65536).to_bytes(4,"big")+bytes(16)
    tkhd=bytes(40)+matrix+(width*65536).to_bytes(4,"big")+(height*65536).to_bytes(4,"big")
    moov=box(b"mvhd",mvhd)+box(b"trak",box(b"tkhd",tkhd)+box(b"mdia",box(b"hdlr",bytes(8)+b"vide"+bytes(12))))
    return box(b"ftyp",b"isom0000")+box(b"mdat",b"x"*4096)+box(b"moov",moov)

class Request:
    def __init__(self, data, headers=None):
        self.data=data
        self.headers=headers or {"content-type":"video/mp4","content-length":str(len(data))}
    async def stream(self):
        yield self.data

class GateTests(TestCase):
    def test_contract_root_and_scope(self):
        self.assertEqual(c.contract()["destinationRoot"],"1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl")
        self.assertFalse(c.contract()["productionClaimAllowedByCanary"])
    def test_wrong_worker_run_sha_mime_and_length(self):
        good=("muse-02",c.RUN_ID,c.SHA256,"video/mp4",str(c.SIZE))
        c.validate_request(*good)
        for idx,val,status in [(0,"muse-01",403),(0,"legacy",403),(1,"wrong",409),(2,"bad",409),(3,"image/png",415),(4,"1",422)]:
            args=list(good); args[idx]=val
            with self.assertRaises(HTTPException) as caught:
                c.validate_request(*args)
            self.assertEqual(caught.exception.status_code,status)
    def test_bytes_sha_duration_and_ratio(self):
        data=fixture()
        with patch.object(c,"SIZE",len(data)),patch.object(c,"SHA256",hashlib.sha256(data).hexdigest()):
            self.assertEqual(c.verify_bytes(data)["durationSeconds"],10.0)
            with self.assertRaises(HTTPException):
                c.verify_bytes(data[:-1]+b"y")
        for data in (fixture(3000),fixture(width=1280,height=720),b"x"*4096):
            with patch.object(c,"SIZE",len(data)),patch.object(c,"SHA256",hashlib.sha256(data).hexdigest()):
                with self.assertRaises(HTTPException):
                    c.verify_bytes(data)

class IngestTests(IsolatedAsyncioTestCase):
    async def test_oversize_before_drive(self):
        with patch.object(c,"SIZE",4):
            with self.assertRaises(HTTPException) as caught:
                await c.read_native(Request(b"12345"))
            self.assertEqual(caught.exception.status_code,413)
    async def test_denied_worker_never_reads_body_or_drive(self):
        with patch.object(c,"provider_and_folder") as drive,patch.object(c,"read_native",AsyncMock()) as read:
            with self.assertRaises(HTTPException) as caught:
                await c.ingest(Request(b"x"),"tenant","muse-01",c.RUN_ID,c.SHA256)
            self.assertEqual(caught.exception.status_code,403)
            drive.assert_not_called()
            read.assert_not_awaited()
    async def test_native_storage_and_readback_twice(self):
        data=fixture(); sha=hashlib.sha256(data).hexdigest()
        async def save(arg):
            self.assertEqual(arg.tenant_id,"tenant")
            self.assertEqual(arg.destination_folder_id,c.FOLDER_ID)
            self.assertEqual(arg.asset_id, f"m02-canary:{c.RUN_ID.replace('-', '')}:{sha}")
            self.assertLessEqual(len(("cam_asset_id" + arg.asset_id).encode("utf-8")), 124)
            self.assertEqual(b"".join([p async for p in arg.body]),data)
            return SimpleNamespace(remote_file_id="native_file_123",remote_folder_id=c.FOLDER_ID)
        async def body():
            yield data
        provider=SimpleNamespace(store_asset=AsyncMock(side_effect=save),
                                 open_asset=AsyncMock(side_effect=lambda arg: SimpleNamespace(body=body(),close=AsyncMock())))
        saved={"id":"native_file_123","parents":[c.FOLDER_ID],"trashed":False,
               "mimeType":"video/mp4","size":str(len(data)),"permissions":[{"type":"user"}]}
        with patch.object(c,"SIZE",len(data)),patch.object(c,"SHA256",sha),patch.object(c,"provider_and_folder",return_value=(provider,c.ROOT_FOLDER_ID)),patch.object(c,"_check_folder",AsyncMock()),patch.object(c,"verify_listing_file_provenance",AsyncMock()),patch.object(c,"_metadata",AsyncMock(return_value=saved)):
            for _ in range(2):
                receipt=await c.ingest(Request(data),"tenant","muse-02",c.RUN_ID,sha)
                self.assertTrue(receipt["storageVerified"])
                self.assertFalse(receipt["productionClaimChanged"])
                self.assertFalse(receipt["productionListingReceiptVerified"])
                self.assertFalse(receipt["readinessRegistryChanged"])
                self.assertEqual(receipt["sha256"],sha)
            self.assertEqual(provider.open_asset.await_count,2)
            self.assertEqual(provider.store_asset.await_count,2)
    async def test_folder_provenance_and_public_readback_fail_closed(self):
        with patch.object(c,"_metadata",AsyncMock(return_value={"id":c.FOLDER_ID,"parents":["other"]})),patch.object(c,"verify_private_drive_destination",AsyncMock()) as private:
            with self.assertRaises(HTTPException) as caught:
                await c._check_folder(object(),"tenant")
            self.assertEqual(caught.exception.status_code,409)
            private.assert_not_awaited()
        bad={"id":"native_file_123","parents":[c.FOLDER_ID],"trashed":False,
             "mimeType":"video/mp4","size":str(c.SIZE),"permissions":[{"type":"anyone"}]}
        provider=SimpleNamespace(open_asset=AsyncMock())
        with patch.object(c,"_metadata",AsyncMock(return_value=bad)):
            with self.assertRaises(HTTPException) as caught:
                await c._readback(provider,"tenant","asset","native_file_123")
            self.assertEqual(caught.exception.status_code,409)
            provider.open_asset.assert_not_awaited()
    async def test_wrong_drive_root_no_write(self):
        data=fixture(); sha=hashlib.sha256(data).hexdigest()
        provider=SimpleNamespace(store_asset=AsyncMock())
        with patch.object(c,"SIZE",len(data)),patch.object(c,"SHA256",sha),patch.object(c,"provider_and_folder",return_value=(provider,"wrong")):
            with self.assertRaises(HTTPException) as caught:
                await c.ingest(Request(data),"tenant","muse-02",c.RUN_ID,sha)
            self.assertEqual(caught.exception.status_code,503)
            provider.store_asset.assert_not_awaited()
