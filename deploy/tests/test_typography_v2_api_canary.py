"""Safety coverage: Typography V2 API-only production canary opt-in."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/cam_typography_v2_api_canary.py"
spec = importlib.util.spec_from_file_location("cam_typography_v2_api_canary", SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
TENANT = "00000000-0000-4000-8000-000000000001"


class CanaryTest(unittest.TestCase):
    def test_only_api_v2_metadata_is_enabled(self):
        text = mod.config_for(TENANT)
        self.assertIn("TYPOGRAPHY_V2_ENABLED=true", text)
        self.assertIn("TYPOGRAPHY_V2_CANARY_TENANT_IDS=" + TENANT, text)
        for flag in ("TYPOGRAPHY_V2_PILOT_WORKER_ENABLED",
                     "TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED",
                     "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED"):
            self.assertIn(flag + "=false", text)
        self.assertNotIn("AUTO_SCOUT_ENABLED=true", text)
        self.assertNotIn("DATABASE_URL", text)
        self.assertNotIn("PGPASSWORD", text)

    def test_owned_dropin_changes_only_api_environment(self):
        text = mod.dropin_text()
        self.assertTrue(text.startswith(mod.MARKER + "\n"))
        self.assertIn("[Service]", text)
        self.assertIn("EnvironmentFile=" + str(mod.CANARY_ENV), text)
        for unsafe in ("ExecStart=", "postgres", "worker", "ImageGen", "systemctl"):
            self.assertNotIn(unsafe, text)

    def test_dry_run_does_not_write_files_or_restart_services(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "override.conf"
            env = Path(directory) / "canary.env"
            with patch.object(mod, "CANARY_ENV", env), patch.object(mod, "DROPIN", target), patch.object(
                mod, "source_gate"
            ), patch.object(mod, "production_gate"), patch.object(
                mod, "db_gate", return_value=TENANT
            ), patch.object(mod.os, "geteuid", return_value=0), patch.object(
                mod, "activate"
            ) as activate, patch.object(mod, "_reload_and_restart") as restart:
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(mod.main([]), 0)
                self.assertIn("CANARY_DRY_RUN=PASS", output.getvalue())
                self.assertIn("BACKUP_CREATED=NO", output.getvalue())
                self.assertNotIn(TENANT, output.getvalue())
                activate.assert_not_called()
                restart.assert_not_called()
                self.assertFalse(target.exists())
                self.assertFalse(env.exists())

    def test_nonroot_cannot_even_read_target_tenant(self):
        with patch.object(mod.os, "geteuid", return_value=1000), patch.object(
            mod, "db_gate"
        ) as db:
            with redirect_stderr(StringIO()):
                self.assertEqual(mod.main(["--apply"]), 2)
            db.assert_not_called()

    def test_database_gate_requires_exact_0189_before_tenant_lookup(self):
        with patch.object(mod, "run", return_value="0187_navbar_member_permissions") as run:
            with self.assertRaises(mod.GateError):
                mod.db_gate()
            self.assertEqual(run.call_count, 1)

    def test_database_gate_requires_single_active_tenant(self):
        with patch.object(mod, "run", side_effect=[mod.VERSION, ""]):
            with self.assertRaises(mod.GateError):
                mod.db_gate()
        with patch.object(mod, "run", side_effect=[mod.VERSION, TENANT + "\n" + TENANT]):
            with self.assertRaises(mod.GateError):
                mod.db_gate()
        with patch.object(mod, "run", side_effect=[mod.VERSION, TENANT]):
            self.assertEqual(mod.db_gate(), TENANT)

    def test_activate_atomic_writes_and_systemd_rebind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "protected" / "canary.env"
            override = root / "unit" / "99-typography-v2-api-canary.conf"
            with patch.object(mod, "CANARY_ENV", env), patch.object(
                mod, "DROPIN", override
            ), patch.object(mod, "_reload_and_restart") as restart, patch.object(
                mod, "run", return_value=str(env) + " (ignore_errors=no)"
            ):
                mod.activate(TENANT)
                self.assertEqual(env.stat().st_mode & 0o777, 0o600)
                self.assertEqual(override.stat().st_mode & 0o777, 0o644)
                self.assertIn(TENANT, env.read_text())
                self.assertTrue(override.read_text().startswith(mod.MARKER))
                restart.assert_called_once_with()

    def test_activate_readiness_failure_rolls_back_exactly_our_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "canary.env"
            override = root / "override.conf"
            with patch.object(mod, "CANARY_ENV", env), patch.object(
                mod, "DROPIN", override
            ), patch.object(mod, "_reload_and_restart", side_effect=[
                mod.GateError("API readiness failed"), None
            ]) as restart:
                with self.assertRaises(mod.GateError):
                    mod.activate(TENANT)
                self.assertFalse(env.exists())
                self.assertFalse(override.exists())
                self.assertEqual(restart.call_count, 2)

    def test_existing_admin_files_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "env"
            override = root / "override"
            env.write_text("ADMIN_CONFIG")
            with patch.object(mod, "CANARY_ENV", env), patch.object(mod, "DROPIN", override):
                with self.assertRaises(mod.GateError):
                    mod.activate(TENANT)
            self.assertEqual(env.read_text(), "ADMIN_CONFIG")
            self.assertFalse(override.exists())

    def test_disable_ignores_unknown_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "env"
            override = root / "override"
            env.write_text(mod.config_for(TENANT))
            override.write_text("CUSTOM CONFIG")
            with patch.object(mod, "CANARY_ENV", env), patch.object(mod, "DROPIN", override):
                with self.assertRaises(mod.GateError):
                    mod.disable(TENANT)
            self.assertTrue(env.exists())
            self.assertEqual(override.read_text(), "CUSTOM CONFIG")

    def test_disable_owned_configuration_and_failure_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "env"
            override = root / "override"
            env.write_text(mod.config_for(TENANT))
            override.write_text(mod.dropin_text().replace(str(mod.CANARY_ENV), str(env)))
            with patch.object(mod, "CANARY_ENV", env), patch.object(
                mod, "DROPIN", override
            ), patch.object(mod, "_reload_and_restart", side_effect=[
                mod.GateError("API restart failed"), None
            ]) as restart:
                with self.assertRaises(mod.GateError):
                    mod.disable(TENANT)
                self.assertEqual(env.read_text(), mod.config_for(TENANT))
                self.assertEqual(override.read_text(), mod.dropin_text())
                self.assertEqual(restart.call_count, 2)
            with patch.object(mod, "CANARY_ENV", env), patch.object(
                mod, "DROPIN", override
            ), patch.object(mod, "_reload_and_restart"):
                mod.disable(TENANT)
                self.assertFalse(env.exists())
                self.assertFalse(override.exists())


if __name__ == "__main__":
    unittest.main()
