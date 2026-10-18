"""Non-destructive contract checks for CAM VPS disk maintenance."""
from pathlib import Path
import subprocess
import unittest

SCRIPT = Path(__file__).with_name("cam-qa-disk-audit.sh")


class DiskAuditTests(unittest.TestCase):
    def test_plan_safe_only_prints_allowlisted_cleanup(self):
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--plan-safe"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        self.assertIn("apt-get clean", proc.stdout)
        self.assertIn("journalctl --vacuum-time=14d", proc.stdout)
        self.assertIn("journalctl --vacuum-size=256M", proc.stdout)
        self.assertIn("No commands executed", proc.stdout)
        self.assertNotIn("docker system prune", proc.stdout)
        self.assertNotIn("rm -rf", proc.stdout)

    def test_cleanup_is_not_default_and_requires_explicit_opt_in(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('mode="${1:---audit}"', source)
        self.assertIn('[[ $EUID -eq 0 ]]', source)
        self.assertIn('--clean-safe) safe_cleanup', source)
        self.assertNotIn("docker system prune", source)
        self.assertNotIn("docker volume prune", source)
        self.assertNotIn("rm -rf", source)
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True, timeout=5)

    def test_unknown_mode_fails_closed(self):
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--delete-everything"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Usage:", proc.stderr)


if __name__ == "__main__":
    unittest.main()
