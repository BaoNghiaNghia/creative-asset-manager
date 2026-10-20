"""No-network regression checks for Drive-verified Codex PNG cleanup guardrails."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest

MODULE_PATH = Path(__file__).with_name("cam-codex-drive-reconcile.py")
spec = importlib.util.spec_from_file_location("cam_codex_drive_reconcile", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_match(root, name="a.png"):
    path = root / name
    content = b"IMAGE-BYTES"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()
    modified = time.time() - 48 * 3600
    os.utime(path, (modified, modified))
    s = path.lstat()
    return path, (digest, "verified-google-drive-id",
                  (path, s.st_size, md5, str(root), s.st_ino, s.st_mtime_ns))


class VerifiedOnlyTests(unittest.TestCase):
    def test_only_matching_verified_file_removed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path, match = make_match(root)
            unproven = root / "unproven.png"
            unproven.write_bytes(b"no evidence")
            self.assertEqual(module.delete_verified([match], min_age_hours=4), 1)
            self.assertFalse(path.exists())
            self.assertTrue(unproven.is_file())

    def test_changed_image_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path, match = make_match(root)
            path.write_bytes(b"DIFFERENT CONTENT")
            self.assertEqual(module.delete_verified([match], min_age_hours=4), 0)
            self.assertTrue(path.exists())

    def test_recent_uploaded_image_is_retained(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path, match = make_match(root)
            os.utime(path, None)
            s = path.lstat()
            match = (match[0], match[1], (path, s.st_size, match[2][2],
                     str(root), s.st_ino, s.st_mtime_ns))
            self.assertEqual(module.delete_verified([match], min_age_hours=4), 0)
            self.assertTrue(path.exists())

    def test_symlink_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            path,match=make_match(root)
            link=root/"other.png"
            link.symlink_to(path)
            fake=(link,match[2][1],match[2][2],str(root),
                  link.lstat().st_ino,link.lstat().st_mtime_ns)
            self.assertEqual(module.delete_verified([(match[0],match[1],fake)],
                                                        min_age_hours=4),0)
            self.assertTrue(link.is_symlink())
            self.assertTrue(path.is_file())

if __name__ == "__main__":
    unittest.main()
