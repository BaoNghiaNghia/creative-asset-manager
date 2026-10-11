"""Negative-path tests for conservative post-reboot fstab repair."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("cam-vps-post-reboot-repair.py")
spec = importlib.util.spec_from_file_location("cam_vps_post_reboot_repair", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

OLD = "/swapfile swap swap defaults 0 0\n"
ACTIVE = "/swapfile-camqa none swap sw,nofail 0 0\n"


class SwapRepairTests(unittest.TestCase):
    def test_legacy_exact_only(self):
        original = "# /swapfile none swap defaults 0 0\n" + ACTIVE + OLD
        self.assertEqual(module.legacy_entry_indices(original), [2])
        converted = module.rewrite_old_swap(original)
        self.assertIn("# CAM 2026-10-11: invalid legacy swap retired; " + OLD, converted)
        self.assertIn(ACTIVE, converted)
        self.assertEqual(module.legacy_entry_indices(converted), [])
        self.assertEqual(module.rewrite_old_swap(converted), converted)

    def test_refuse_without_verified_active_cam_swap(self):
        with self.assertRaisesRegex(RuntimeError, "absent"):
            module.validate_fstab(ACTIVE + OLD, swaps=set())

    def test_refuse_without_persisted_cam_swap(self):
        with self.assertRaisesRegex(RuntimeError, "no live fstab"):
            module.validate_fstab(OLD, swaps={module.ACTIVE_SWAP})

    def test_refuse_ambiguous_old_swap(self):
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            module.validate_fstab(ACTIVE + OLD + OLD, swaps={module.ACTIVE_SWAP})

    def test_fix_with_backup_and_no_second_rewrite(self):
        with tempfile.TemporaryDirectory() as temp:
            fstab = Path(temp) / "fstab"
            original = "# filesystem entries\n" + ACTIVE + OLD
            fstab.write_text(original, encoding="utf-8")
            backup = module.repair_fstab(fstab, swaps={module.ACTIVE_SWAP})
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            repaired = fstab.read_text(encoding="utf-8")
            self.assertEqual(module.legacy_entry_indices(repaired), [])
            self.assertIn(ACTIVE, repaired)
            self.assertIsNone(module.repair_fstab(fstab, swaps={module.ACTIVE_SWAP}))

    def test_refuse_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target"
            target.write_text(ACTIVE + OLD)
            symlink = Path(temp) / "fstab"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "non-symlink"):
                module.repair_fstab(symlink, swaps={module.ACTIVE_SWAP})
            self.assertEqual(target.read_text(), ACTIVE + OLD)


if __name__ == "__main__":
    unittest.main()
