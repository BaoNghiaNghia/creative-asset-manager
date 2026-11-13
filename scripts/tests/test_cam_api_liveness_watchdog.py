"""Offline, side-effect-free tests for the root-only CAM API watchdog."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts" / "cam-api-liveness-watchdog.py"
SPEC = importlib.util.spec_from_file_location("cam_api_liveness_watchdog", SOURCE)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ApiLivenessWatchdogTests(unittest.TestCase):
    def test_guard_skips_service_in_startup_or_cooldown(self):
        self.assertFalse(module.eligible(True, 100, 119, 999))
        self.assertFalse(module.eligible(True, 100, 121, 899))
        self.assertFalse(module.eligible(False, 100, 121, 999))
        self.assertFalse(module.eligible(True, 0, 121, 999))
        self.assertTrue(module.eligible(True, 100, 121, 900))

    def test_successful_readiness_does_not_restart(self):
        with tempfile.TemporaryDirectory() as tmp, \
            patch.object(module, "STATE_DIR", Path(tmp)), \
            patch.object(module, "service_pid", return_value=123), \
            patch.object(module, "service_active", return_value=True), \
            patch.object(module, "service_age_seconds", return_value=400), \
            patch.object(module, "ready", return_value=True) as probe, \
            patch.object(module.subprocess, "run") as restart:
            self.assertEqual(module.main(), 0)
            self.assertEqual(probe.call_count, 1)
            restart.assert_not_called()

    def test_two_failures_restart_only_api_and_enter_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp, \
            patch.object(module, "STATE_DIR", Path(tmp)), \
            patch.object(module, "service_pid", return_value=123), \
            patch.object(module, "service_active", return_value=True), \
            patch.object(module, "service_age_seconds", return_value=400), \
            patch.object(module, "ready", side_effect=[False, False]) as probe, \
            patch.object(module.time, "sleep") as sleep, \
            patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as restart:
            self.assertEqual(module.main(), 0)
            self.assertEqual(probe.call_count, 2)
            sleep.assert_called_once_with(module.PROBE_GAP_SECONDS)
            restart.assert_called_once_with(
                ["systemctl", "--no-block", "restart", "creative-asset-manager-api.service"],
                check=False, capture_output=True, timeout=8,
            )
            self.assertTrue((Path(tmp) / "last-restart-epoch").exists())

    def test_replaced_process_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as tmp, \
            patch.object(module, "STATE_DIR", Path(tmp)), \
            patch.object(module, "service_pid", side_effect=[123, 124]), \
            patch.object(module, "service_active", return_value=True), \
            patch.object(module, "service_age_seconds", return_value=400), \
            patch.object(module, "ready", side_effect=[False, False]), \
            patch.object(module.time, "sleep"), \
            patch.object(module.subprocess, "run") as restart:
            self.assertEqual(module.main(), 0)
            restart.assert_not_called()

    def test_recent_restart_disables_probing(self):
        with tempfile.TemporaryDirectory() as tmp, \
            patch.object(module, "STATE_DIR", Path(tmp)), \
            patch.object(module, "service_pid", return_value=123), \
            patch.object(module, "service_active", return_value=True), \
            patch.object(module, "service_age_seconds", return_value=400), \
            patch.object(module, "ready") as probe, \
            patch.object(module.time, "time", return_value=10000):
            (Path(tmp) / "last-restart-epoch").write_text("9900")
            self.assertEqual(module.main(), 0)
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
