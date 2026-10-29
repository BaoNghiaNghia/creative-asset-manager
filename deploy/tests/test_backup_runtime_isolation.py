"""Safeguards for isolated CAM PostgreSQL backup runtime (no live DB required)."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "cam_isolate_db_backup_runtime.py"
spec = importlib.util.spec_from_file_location("cam_backup_runtime_isolation", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
COMMIT = "a" * 40


class BackupRuntimeIsolationTest(unittest.TestCase):
    def test_owned_systemd_dropin_is_only_backup_service(self):
        path = Path("/opt/creative-asset-manager/releases") / COMMIT
        text = module.unit_override(path)
        self.assertTrue(text.startswith(module.OWNER_MARKER + "\n"))
        self.assertIn("WorkingDirectory=" + str(path / "apps/api"), text)
        self.assertIn(
            "ExecStart=" + str(path / "apps/api/.venv/bin/python")
            + " -m app.operations.database_backup_cli backup",
            text,
        )
        for forbidden in ("systemctl restart", "alembic", "DROP TABLE", "pg_dump", "worker.service"):
            self.assertNotIn(forbidden, text)

    def test_only_main_ancestor_with_immutable_release_marker_can_be_selected(self):
        with tempfile.TemporaryDirectory() as path:
            release = Path(path) / "releases" / COMMIT
            source = release / "apps/api/app/modules/database_backup"
            source.mkdir(parents=True)
            cli = release / "apps/api/app/operations"
            cli.mkdir(parents=True)
            py = release / "apps/api/.venv/bin"
            py.mkdir(parents=True)
            (release / ".cam-release").write_text(COMMIT + "\n")
            (release / ".cam-release-ready").touch()
            (py / "python").touch(mode=0o755)
            (source / "service.py").write_text("DATABASE_BACKUP_DATABASE_URL")
            (cli / "database_backup_cli.py").write_text("verify-config")
            with patch.object(module, "APP_ROOT", Path(path)):
                with patch.object(module, "command", return_value="") as command:
                    self.assertEqual(module.release_path(COMMIT, "b" * 40), release)
                    self.assertEqual(command.call_count, 2)
                    command.assert_any_call(
                        "git", "-C", str(module.SOURCE), "merge-base",
                        "--is-ancestor", COMMIT, "b" * 40,
                    )
                (release / ".cam-release").write_text("different")
                with self.assertRaises(module.BackupRuntimeGateError):
                    module.release_path(COMMIT, "b" * 40)

    def test_short_sha_and_invalid_commit_rejected(self):
        with self.assertRaises(module.BackupRuntimeGateError):
            module.release_path("a" * 7, "b" * 40)
        with patch.object(module, "command", side_effect=module.BackupRuntimeGateError("bad ancestor")):
            with self.assertRaises(module.BackupRuntimeGateError):
                module.release_path(COMMIT, "b" * 40)

    def test_preflight_uses_read_only_verify_config_not_backup(self):
        path = Path("/opt/creative-asset-manager/releases") / COMMIT
        with patch.object(module, "command", return_value="") as call:
            module.config_verify(path)
        args = call.call_args.args
        self.assertEqual(args[0], "systemd-run")
        self.assertIn("verify-config", args)
        self.assertNotIn("backup", args)
        self.assertIn("--property=NoNewPrivileges=yes", args)
        self.assertIn("--property=ProtectSystem=strict", args)
        self.assertIn("--property=User=creative-assets", args)
        self.assertIn("--property=EnvironmentFile=" + str(module.BACKUP_ENV), args)
        self.assertNotIn("DATABASE_BACKUP_DATABASE_URL", " ".join(args))

    def test_apply_pins_unit_after_successful_preflight_without_running_dump(self):
        path = Path("/opt/creative-asset-manager/releases") / COMMIT
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def system(*args, **kwargs):
                calls.append(args)
                if args[:3] == ("systemctl", "show", module.SERVICE):
                    show_count = sum(c[:3] == ("systemctl", "show", module.SERVICE) for c in calls)
                    return (str(module.APP_ROOT / "current/apps/api/.venv/bin/python")
                            if show_count == 1 else str(path / "apps/api/.venv/bin/python"))
                return ""

            with patch.object(module, "DROPIN_ROOT", root), patch.object(
                module, "DROPIN", root / "90-cam-isolated-backup-runtime.conf"
            ), patch.object(module, "config_verify") as verify, patch.object(
                module, "command", side_effect=system
            ), patch.object(module.os, "geteuid", return_value=0):
                module.apply(path)
                verify.assert_called_once_with(path)
                self.assertTrue((root / "90-cam-isolated-backup-runtime.conf").is_file())
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[0][:3], ("systemctl", "show", module.SERVICE))
                self.assertEqual(calls[1][:2], ("systemctl", "daemon-reload"))
                self.assertEqual(calls[2][:3], ("systemctl", "show", module.SERVICE))

    def test_apply_preflight_failure_leaves_no_override(self):
        path = Path("/opt/creative-asset-manager/releases") / COMMIT
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(module, "DROPIN_ROOT", root), patch.object(
                module, "DROPIN", root / "90-cam-isolated-backup-runtime.conf"
            ), patch.object(module, "config_verify", side_effect=module.BackupRuntimeGateError("bad settings")):
                with self.assertRaises(module.BackupRuntimeGateError):
                    module.apply(path)
                self.assertEqual(list(root.iterdir()), [])

    def test_apply_bad_systemd_binding_rolls_back_only_own_override(self):
        path = Path("/opt/creative-asset-manager/releases") / COMMIT
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def system(*args, **kwargs):
                if args[:3] == ("systemctl", "show", module.SERVICE):
                    return "/opt/creative-asset-manager/current/apps/api/.venv/bin/python"
                return ""

            with patch.object(module, "DROPIN_ROOT", root), patch.object(
                module, "DROPIN", root / "90-cam-isolated-backup-runtime.conf"
            ), patch.object(module, "config_verify"), patch.object(
                module, "command", side_effect=system
            ), patch.object(module.os, "geteuid", return_value=0):
                with self.assertRaises(module.BackupRuntimeGateError):
                    module.apply(path)
                self.assertFalse((root / "90-cam-isolated-backup-runtime.conf").exists())

    def test_main_dry_run_does_not_call_config_verify_or_change_systemd(self):
        with patch.object(module, "git_authority", return_value="b" * 40), patch.object(
            module, "preflight_unit"
        ), patch.object(
            module, "release_path", return_value=Path("/opt/releases") / COMMIT
        ), patch.object(module, "config_verify") as verify, patch.object(
            module, "apply"
        ) as activate, patch.object(
            module, "DROPIN", Path("/nonexistent/cam-backup-runtime") / COMMIT
        ):
            output = StringIO()
            with redirect_stdout(output):
                code = module.main(["--commit", COMMIT])
            self.assertEqual(code, 0)
            self.assertIn("BACKUP_RUNTIME_DRY_RUN=YES", output.getvalue())
            self.assertIn("FULL_DUMP_STARTED=NO", output.getvalue())
            verify.assert_not_called()
            activate.assert_not_called()

    def test_main_refuses_unknown_arguments_or_malformed_commit(self):
        stream = StringIO()
        with redirect_stderr(stream), patch.object(
            module, "git_authority", return_value="b" * 40
        ), patch.object(module, "preflight_unit"):
            self.assertEqual(module.main(["--commit", "short"]), 2)
        self.assertIn("BACKUP_RUNTIME_FAILED=", stream.getvalue())

    def test_restore_to_supported_current_unit_is_reversible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_release = root / "releases" / COMMIT
            code = current_release / "apps/api/app/modules/database_backup"
            code.mkdir(parents=True)
            (code / "service.py").write_text("DATABASE_BACKUP_DATABASE_URL")
            (root / "current").symlink_to(current_release)
            override = root / "override.conf"
            override.write_text(module.OWNER_MARKER + "\n[Service]\n")
            with patch.object(module, "APP_ROOT", root), patch.object(
                module, "DROPIN", override
            ), patch.object(module, "config_verify") as verify, patch.object(
                module.os, "geteuid", return_value=0
            ), patch.object(module, "command", return_value=str(root / "current/apps/api/.venv/bin/python")):
                module.restore_current(do_apply=True)
                verify.assert_called_once_with(current_release)
                self.assertFalse(override.exists())

    def test_failed_restore_preserves_pinned_service_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "releases" / COMMIT
            code = release / "apps/api/app/modules/database_backup"
            code.mkdir(parents=True)
            (code / "service.py").write_text("DATABASE_BACKUP_DATABASE_URL")
            (root / "current").symlink_to(release)
            override = root / "override.conf"
            original = (module.OWNER_MARKER + "\n[Service]\n").encode()
            override.write_bytes(original)
            with patch.object(module, "APP_ROOT", root), patch.object(
                module, "DROPIN", override
            ), patch.object(module, "config_verify"), patch.object(
                module.os, "geteuid", return_value=0
            ), patch.object(module, "command", return_value="invalid original unit"):
                with self.assertRaises(module.BackupRuntimeGateError):
                    module.restore_current(do_apply=True)
                self.assertEqual(override.read_bytes(), original)

    def test_cleanup_paths_protect_pinned_backup_runtime(self):
        backend = (ROOT / "scripts/cam-rebuild-backend.sh").read_text()
        cleanup = (ROOT / "scripts/cam-safe-disk-cleanup.sh").read_text()
        for source in (backend, cleanup):
            self.assertIn("90-cam-isolated-backup-runtime.conf", source)
            self.assertIn("CAM isolated backup runtime v1", source)
            self.assertIn("backup-runtime", source.replace("_", "-"))
            self.assertIn(
                " -m app.operations.database_backup_cli backup", source
            )
        self.assertIn('protect_release "$release"', backend)
        self.assertIn('"$target" == "$pinned_backup"', cleanup)


if __name__ == "__main__":
    unittest.main()
