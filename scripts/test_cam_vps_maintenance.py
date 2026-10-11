"""Tests of restricted maintenance behavior; never alter real /etc or services."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).with_name("cam-vps-maintenance.py")
spec = importlib.util.spec_from_file_location("cam_vps_maint", PATH)
maint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maint)

OLD = "/swapfile swap swap defaults 0 0\n"
ACTIVE = "/swapfile-camqa none swap sw,nofail 0 0\n"


class BrokerTests(unittest.TestCase):
    def test_broker_queries_swapon_in_show_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fstab = base / "fstab"
            fstab.write_text(ACTIVE + OLD)
            state = base / "state"
            state.mkdir()
            calls = []
            original = maint.checked_run
            try:
                def mock_checked_run(argv, *, timeout=30):
                    calls.append(argv)
                    class Result:
                        stdout = maint.LIVE_SWAP + "\n" if argv[0] == "swapon" else ""
                    return Result()
                maint.checked_run = mock_checked_run
                maint.perform("swap-repair", fstab=fstab, state_dir=state)
                self.assertEqual(calls[0],
                    ["swapon", "--show=NAME", "--noheadings", "--raw"])
                self.assertEqual(calls[1:], [
                    ["systemctl", "daemon-reload"],
                    ["systemctl", "reset-failed", "swapfile.swap"],
                ])
            finally:
                maint.checked_run = original

    def test_swap_fix_and_exact_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fstab = base / "fstab"
            fstab.write_text("# rootfs\n" + ACTIVE + OLD)
            state = base / "state"
            state.mkdir()
            saved = fstab.read_text()
            result = maint.do_swap_repair(
                fstab=fstab, backup=state / "fstab-before-swap-repair",
                marker=state / "fstab-repair.json", active_names={maint.LIVE_SWAP})
            self.assertIn("retired", result)
            self.assertIn("# CAM: retired invalid legacy swap;", fstab.read_text())
            self.assertEqual((state / "fstab-before-swap-repair").read_text(), saved)
            self.assertIn("restored", maint.do_swap_rollback(
                fstab=fstab, backup=state / "fstab-before-swap-repair",
                marker=state / "fstab-repair.json", active_names={maint.LIVE_SWAP}))
            self.assertEqual(fstab.read_text(), saved)

    def test_no_rollback_over_operator_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fstab = base / "fstab"
            fstab.write_text(ACTIVE + OLD)
            state = base / "state"
            state.mkdir()
            kwargs = dict(fstab=fstab, backup=state / "fstab-before-swap-repair",
                          marker=state / "fstab-repair.json", active_names={maint.LIVE_SWAP})
            maint.do_swap_repair(**kwargs)
            fstab.write_text(fstab.read_text() + "# independent edit\n")
            with self.assertRaisesRegex(RuntimeError, "changed since repair"):
                maint.do_swap_rollback(**kwargs)

    def test_refuse_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real = base / "real"
            real.write_text(ACTIVE + OLD)
            fstab = base / "fstab"
            fstab.symlink_to(real)
            with self.assertRaisesRegex(RuntimeError, "non-symlink"):
                maint.do_swap_repair(fstab=fstab, backup=base / "bak",
                                     marker=base / "marker", active_names={maint.LIVE_SWAP})
            self.assertEqual(real.read_text(), ACTIVE + OLD)

    def test_refuse_without_active_safe_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fstab = base / "fstab"
            fstab.write_text(ACTIVE + OLD)
            with self.assertRaisesRegex(RuntimeError, "Active CAM swap"):
                maint.do_swap_repair(fstab=fstab, backup=base / "bak",
                                     marker=base / "marker", active_names=set())

    def test_refuse_ambiguous_multiple_legacy_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fstab = base / "fstab"
            fstab.write_text(ACTIVE + OLD + OLD)
            with self.assertRaisesRegex(RuntimeError, "More than one"):
                maint.do_swap_repair(fstab=fstab, backup=base / "bak",
                                     marker=base / "marker", active_names={maint.LIVE_SWAP})

    def test_root_state_path_is_outside_application_writable_tree(self):
        self.assertEqual(maint.STATE_DIR, Path("/var/lib/cam-vps-maintenance"))
        self.assertEqual(maint.LOG_DIR, Path("/var/log/cam-vps-maintenance"))

    def test_only_five_actions_have_a_handler(self):
        self.assertEqual(set(maint.ALLOWED), {
            "status", "swap-repair", "swap-rollback",
            "network-audit", "stage1-retention-retry",
        })
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            maint.perform("shell-root", active_names={maint.LIVE_SWAP})

    def test_retry_action_has_fixed_unit(self):
        calls = []
        original = maint.checked_run
        try:
            def track(argv, *, timeout=30):
                calls.append((argv, timeout))
                class Result:
                    stdout = ""
                return Result()
            maint.checked_run = track
            message = maint.perform("stage1-retention-retry")
            self.assertIn("Stage 1", message)
            self.assertEqual(calls, [
                (["systemctl", "start",
                  "creative-asset-manager-stage1-temp-retention.service"], 180)
            ])
        finally:
            maint.checked_run = original


if __name__ == "__main__":
    unittest.main()
