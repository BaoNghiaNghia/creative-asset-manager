"""Regression tests for safe, repo-scoped orphaned Vite preview cleanup."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("cam-clean-orphaned-ui-qa.py")
spec = importlib.util.spec_from_file_location("cam_clean_orphans", SCRIPT)
cleaner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaner)

PREFIX = "node ./apps/client/node_modules/.bin/vite preview --host 127.0.0.1 --port"


def ps_line(pid: int, ppid: int, age: int, command: str) -> str:
    return f"{pid} {ppid} {age} {command}"


class OrphanPreviewTests(TestCase):
    def test_detects_actual_vite_preview_argv_and_ignores_production(self):
        report = "\n".join([
            ps_line(101, 1, 2400, PREFIX + " 61321 --strictPort"),
            ps_line(102, 1, 2400, PREFIX + " 61322 --strictPort"),
            ps_line(103, 222, 2400, PREFIX + " 61324 --strictPort"),
            ps_line(104, 1, 100, PREFIX + " 61325 --strictPort"),
            ps_line(105, 1, 2400, "python -m uvicorn app.main:app --port 8000"),
            ps_line(106, 1, 2400, PREFIX + " 61326 --strictPort --public"),
            ps_line(107, 1, 2400, "node ./apps/client/node_modules/.bin/vite --host 127.0.0.1 --port 61327"),
            ps_line(108, 1, 2400, PREFIX + " 61328 --strictPort" + " " * 20),
        ])
        self.assertEqual(cleaner.orphaned_vite_servers(report=report), [101, 102, 108])

    def test_rejects_any_public_host_or_non_qa_process_shape(self):
        variants = [
            PREFIX.replace("127.0.0.1", "0.0.0.0") + " 61321 --strictPort",
            PREFIX.replace("127.0.0.1", "::") + " 61321 --strictPort",
            PREFIX + " 99999 --strictPort",
            PREFIX + " 543 --strictPort",
            "python ./apps/client/node_modules/.bin/vite preview --host 127.0.0.1 --port 61321 --strictPort",
            "node /tmp/evil/vite preview --host 127.0.0.1 --port 61321 --strictPort",
        ]
        report = "\n".join(ps_line(i + 10, 1, 2400, v) for i, v in enumerate(variants))
        self.assertEqual(cleaner.orphaned_vite_servers(report=report), [])

    def test_apply_revalidates_pid_command_parent_and_repo_cwd(self):
        old = ps_line(201, 1, 2400, PREFIX + " 61321 --strictPort")
        new = ps_line(201, 1, 2400, PREFIX + " 61321 --strictPort")
        with patch.object(cleaner.subprocess, "check_output", side_effect=[old, new]), \
             patch.object(cleaner.os, "readlink", return_value=str(cleaner.REPO)), \
             patch.object(cleaner.os, "kill") as terminate, \
             patch.object(cleaner.time, "sleep"):
            self.assertEqual(cleaner.cleanup(apply=True), 1)
        terminate.assert_called_once_with(201, cleaner.signal.SIGTERM)

    def test_refuses_pid_reuse_and_other_workspace_cwd(self):
        old = ps_line(202, 1, 2400, PREFIX + " 61321 --strictPort")
        reused = ps_line(202, 1, 2400, PREFIX + " 61322 --strictPort")
        with patch.object(cleaner.subprocess, "check_output", side_effect=[old, reused]), \
             patch.object(cleaner.os, "readlink", return_value=str(cleaner.REPO)), \
             patch.object(cleaner.os, "kill") as terminate:
            self.assertEqual(cleaner.cleanup(apply=True), 0)
            terminate.assert_not_called()
        with patch.object(cleaner.subprocess, "check_output", side_effect=[old, old]), \
             patch.object(cleaner.os, "readlink", return_value="/opt/other-app"), \
             patch.object(cleaner.os, "kill") as terminate:
            self.assertEqual(cleaner.cleanup(apply=True), 0)
            terminate.assert_not_called()

    def test_dry_run_never_signals_process(self):
        old = ps_line(203, 1, 2400, PREFIX + " 61321 --strictPort")
        with patch.object(cleaner.subprocess, "check_output", return_value=old), \
             patch.object(cleaner.os, "kill") as terminate:
            self.assertEqual(cleaner.cleanup(apply=False), 1)
            terminate.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
