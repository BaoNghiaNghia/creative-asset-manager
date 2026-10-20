"""Archive guard tests; never access production Drive."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest

path=Path(__file__).with_name("cam-codex-drive-archive.py")
spec=importlib.util.spec_from_file_location("cam_codex_drive_archive",path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixture(root, *, hours=48):
    p=root/"source.png"
    p.write_bytes(b"valid original")
    ts=time.time()-hours*3600
    os.utime(p,(ts,ts))
    return p,p.lstat(),*mod.checksums(p)


class ArchiveRetentionTests(unittest.TestCase):
    def test_verified_original_can_be_released(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            p,st,sha,md5=fixture(root)
            self.assertTrue(mod.remove_exact(p,root,st,sha,md5,time.time()-4*3600))
            self.assertFalse(p.exists())

    def test_changed_file_not_removed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            p,st,sha,md5=fixture(root)
            p.write_bytes(b"different bytes")
            self.assertFalse(mod.remove_exact(p,root,st,sha,md5,time.time()-4*3600))
            self.assertTrue(p.exists())

    def test_recent_file_not_removed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            p,st,sha,md5=fixture(root,hours=1)
            self.assertFalse(mod.remove_exact(p,root,st,sha,md5,time.time()-4*3600))
            self.assertTrue(p.exists())

    def test_untrusted_symlink_not_removed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            p,st,sha,md5=fixture(root)
            link=root/"symlink.png"
            link.symlink_to(p)
            self.assertFalse(mod.remove_exact(link,root,st,sha,md5,time.time()-4*3600))
            self.assertTrue(p.is_file())

if __name__=="__main__":
    unittest.main()
