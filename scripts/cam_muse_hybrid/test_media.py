"""Native container fixture tests; no decoder, renderer, or external binary."""
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core import HybridBlocked, Journal, Coordinator, GatewayAdapter, digest
from media import MediaSpool, validate_native_mp4
from test_core import make_intent, MockGateway, ORIGINAL, HERO, TASK, PROMPT


def atom(kind,payload):
    return (len(payload)+8).to_bytes(4,"big")+kind+payload


def native_fixture(width=288,height=512,ticks=6000):
    # Synthetic ISO BMFF headers for container tests only, not a playable clip.
    mvhd=bytes(12)+(1000).to_bytes(4,"big")+ticks.to_bytes(4,"big")+bytes(80)
    matrix=(65536).to_bytes(4,"big")+bytes(12)+(65536).to_bytes(4,"big")+bytes(16)
    tkhd=bytes(40)+matrix+(width*65536).to_bytes(4,"big")+(height*65536).to_bytes(4,"big")
    hdlr=bytes(8)+b"vide"+bytes(12)
    moov=atom(b"mvhd",mvhd)+atom(b"trak",atom(b"tkhd",tkhd)+atom(b"mdia",atom(b"hdlr",hdlr)))
    return atom(b"ftyp",b"isom0000")+atom(b"moov",moov)+atom(b"mdat",b"x"*4096)


class MediaTests(unittest.TestCase):
    mp4=native_fixture()

    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.intent=make_intent()
        self.key=self.intent.identity.key
        self.mock=MockGateway()

    def prepare(self,journal):
        c=Coordinator(journal,GatewayAdapter(self.mock.request,model="mock-video",
                      compatibility_verified=True),lambda _:True)
        c.submit(self.intent,original_png=ORIGINAL,hero_png=HERO,
                 hero_artwork_sha=self.intent.identity.artwork_sha256,prompt=PROMPT)
        self.mock.status_result=(200,{"id":TASK,"status":"succeeded","partial":False,
            "media":[{"kind":"video","url":"fixture://video"}]}, {})
        self.assertEqual(c.poll(self.key)["state"],"result_ready")
        return c

    def test_native_container_metadata(self):
        meta=validate_native_mp4(self.mp4,"9:16")
        self.assertEqual(meta["mediaValidation"],"native_container_only")
        self.assertEqual((meta["width"],meta["height"]),(288,512))
        self.assertEqual(meta["durationSeconds"],6.0)
        self.assertEqual(meta["sha256"],digest(self.mp4))
        self.assertTrue(meta["nativeBytesPreserved"])

    def test_wrong_aspect_and_truncated_file(self):
        with self.assertRaisesRegex(HybridBlocked,"NATIVE_MP4_ASPECT_MISMATCH"):
            validate_native_mp4(self.mp4,"16:9")
        with self.assertRaises(HybridBlocked):
            validate_native_mp4(self.mp4[:-1],"9:16")
        with self.assertRaises(HybridBlocked):
            validate_native_mp4(b"not a video","9:16")

    def test_unknown_duration_and_short_native_export_accepted(self):
        self.assertNotIn("durationSeconds",validate_native_mp4(native_fixture(ticks=0),"9:16"))
        self.assertEqual(validate_native_mp4(native_fixture(ticks=3000),"9:16")["durationSeconds"],3.0)

    def test_exact_task_binding_before_file_write(self):
        with Journal(self.temp.name) as j:
            self.prepare(j)
            spool=MediaSpool(j)
            with self.assertRaisesRegex(HybridBlocked,"TASK_MEDIA_NOT_CURRENT"):
                spool.stage(self.key,"task_"+"f"*24,self.mp4,lambda *_:True)
            with self.assertRaisesRegex(HybridBlocked,"TASK_MEDIA_BINDING_UNVERIFIED"):
                spool.stage(self.key,TASK,self.mp4,lambda *_:False)
            self.assertFalse(spool._path(self.key).exists())

    def test_file_preserved_until_cam_ack_and_cleanup_idempotent(self):
        with Journal(self.temp.name) as j:
            c=self.prepare(j)
            spool=MediaSpool(j)
            meta=spool.stage(self.key,TASK,self.mp4,lambda *_:True)
            self.assertEqual(spool.stage(self.key,TASK,self.mp4,lambda *_:True),meta)
            with self.assertRaisesRegex(HybridBlocked,"CAM_ACK_REQUIRED"):
                spool.cleanup_verified(self.key)
            c.bind_validated_media(self.key,meta["sha256"],lambda *_:True)
        with Journal(self.temp.name) as j:
            c=Coordinator(j,GatewayAdapter(self.mock.request,model="mock-video",
                          compatibility_verified=True),lambda _:True)
            spool=MediaSpool(j)
            self.assertEqual(spool.read_bound_media(self.key,meta["sha256"]),self.mp4)
            with self.assertRaisesRegex(HybridBlocked,"CAM_RECEIPT_NOT_VERIFIED"):
                c.confirm_receipt(self.key,"mock-receipt",lambda *_:False)
            self.assertTrue(spool._path(self.key).exists())
            c.confirm_receipt(self.key,"mock-receipt",lambda *_:True)
            self.assertTrue(spool.cleanup_verified(self.key))
            self.assertFalse(spool.cleanup_verified(self.key))

    def test_capacity_never_evicts_unacknowledged(self):
        with Journal(self.temp.name) as j:
            self.prepare(j)
            spool=MediaSpool(j)
            existing=spool.root/("f"*64+".mp4")
            existing.write_bytes(self.mp4)
            with patch("media.SPOOL_CAP_BYTES",len(self.mp4)+1):
                with self.assertRaisesRegex(HybridBlocked,"MEDIA_SPOOL_FULL"):
                    spool.stage(self.key,TASK,self.mp4,lambda *_:True)
            self.assertEqual(existing.read_bytes(),self.mp4)
            self.assertFalse(spool._path(self.key).exists())

    def test_changed_bytes_never_cleaned_as_verified(self):
        with Journal(self.temp.name) as j:
            c=self.prepare(j)
            spool=MediaSpool(j)
            sha=spool.stage(self.key,TASK,self.mp4,lambda *_:True)["sha256"]
            c.bind_validated_media(self.key,sha,lambda *_:True)
            c.confirm_receipt(self.key,"mock-receipt",lambda *_:True)
            path=spool._path(self.key)
            path.write_bytes(self.mp4+b"changed")
            with self.assertRaisesRegex(HybridBlocked,"MEDIA_SHA_CHANGED"):
                spool.cleanup_verified(self.key)
            self.assertTrue(path.exists())


if __name__=="__main__":
    unittest.main()