from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPOSITORY_ROOT / "scripts" / "setup-admin.sh"


class SetupAdminScriptTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "apps" / "api").mkdir(parents=True)
        self.venv = self.root / "venv"
        (self.venv / "bin").mkdir(parents=True)
        self.env_file = self.root / "test.env"
        self.env_file.write_text(
            "PERSISTENT_AUTH_ENABLED=true\n"
            "OPENAI_API_KEY=never-print-openai-secret\n"
            "GOOGLE_CLIENT_SECRET=never-print-google-secret\n",
            encoding="utf-8",
        )
        self.log = self.root / "calls.log"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self._write_executable(
            self.fake_bin / "id",
            "#!/bin/sh\nprintf '%s\\n' \"$FAKE_USER\"\n",
        )
        self._write_executable(self.venv / "bin" / "python", self._fake_python())

    def tearDown(self):
        self.temporary.cleanup()

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _fake_python(self) -> str:
        return r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG"
if [ "$1" = "-c" ]; then
  exec /usr/bin/python3 "$@"
fi
case "$*" in
  *"check-admin-setup"*)
    if [ "${FAKE_NOT_HEAD:-}" = "1" ]; then
      printf '%s\n' "Database schema is not at Alembic head" >&2
      exit 1
    fi
    printf '%s\n' '{"database_reachable":true,"alembic_head":"0028_central_authorization","rbac_enabled":true,"legacy_authorization_enabled":false}'
    ;;
  *"list-identities"*)
    status="${FAKE_USER_STATUS:-active}"
    printf '%s\n' "{\"identities\":[{\"identity_id\":\"identity-1\",\"user_id\":\"user-1\",\"provider\":\"google\",\"masked_email\":\"o***@example.com\",\"subject_short\":\"stable...ject\",\"user_status\":\"$status\"}]}"
    ;;
  *"resolve-identity-reference"*)
    printf '%s\n' '{"provider":"google","subject":"stable-provider-subject","user_id":"user-1","user_status":"active"}'
    ;;
  *"app.operations.auth_cli bootstrap-access"*"--dry-run"*)
    printf '%s\n' '{"user_id":"user-1","tenant_id":"tenant-1","membership_id":"membership-1","tenant_created":true,"membership_created":true,"permissions_created":18,"roles_created":4,"role_permissions_created":29,"dry_run":true}'
    ;;
  *"app.operations.auth_cli bootstrap-access"*)
    printf '%s\n' '{"user_id":"user-1","tenant_id":"tenant-1","membership_id":"membership-1","tenant_created":false,"membership_created":false,"permissions_created":0,"roles_created":0,"role_permissions_created":0,"dry_run":false}'
    ;;
  *"grant-platform-admin"*)
    printf '%s\n' '{"user_id":"user-1","assignment_id":"platform-1","status":"active","dry_run":false}'
    ;;
  *"verify-bootstrap-access"*)
    case "$*" in *"--expect-platform-admin"*) platform=true ;; *) platform=false ;; esac
    printf '%s\n' "{\"user_id\":\"user-1\",\"tenant_id\":\"tenant-1\",\"tenant_slug\":\"studio\",\"roles\":[\"tenant_admin\"],\"permissions_verified\":[\"ai_operations.read\",\"tenant_members.manage\"],\"platform_admin\":$platform}"
    ;;
  *)
    printf '%s\n' "unexpected fake python call" >&2
    exit 3
    ;;
esac
'''

    def run_script(
        self,
        *extra: str,
        user: str = "baonghia",
        environment: str | None = "local",
        input_text: str | None = None,
        env_updates: dict[str, str] | None = None,
        use_env_override: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "bash",
            str(SCRIPT),
            "--project-root",
            str(self.root),
            "--venv",
            str(self.venv),
        ]
        if use_env_override:
            command.extend(["--env-file", str(self.env_file)])
        if environment is not None:
            command.extend(["--environment", environment])
        command.extend(extra)
        environment_values = {
            **os.environ,
            "PATH": str(self.fake_bin) + ":/usr/bin:/bin",
            "FAKE_USER": user,
            "FAKE_LOG": str(self.log),
        }
        if env_updates:
            environment_values.update(env_updates)
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            env=environment_values,
            timeout=15,
            check=False,
        )

    @property
    def required_args(self) -> tuple[str, ...]:
        return (
            "--provider", "google",
            "--subject", "stable-provider-subject",
            "--tenant-slug", "studio",
            "--tenant-name", "Studio",
        )

    def test_detects_local_user_and_production_user(self):
        local = self.run_script(*self.required_args, "--yes", environment=None)
        self.assertEqual(local.returncode, 0, local.stderr)
        self.assertIn("Environment: local", local.stdout)

        production = self.run_script(
            *self.required_args,
            "--yes",
            environment=None,
            user="desify",
        )
        self.assertEqual(production.returncode, 0, production.stderr)
        self.assertIn("Environment: production", production.stdout)

    def test_missing_environment_file_fails_before_database_access(self):
        self.env_file.unlink()
        result = self.run_script(*self.required_args, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Environment file does not exist", result.stderr)
        self.assertFalse(self.log.exists())

    def test_local_defaults_fall_back_to_api_environment_file(self):
        api_env = self.root / "apps" / "api" / ".env"
        api_env.write_text(
            self.env_file.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        result = self.run_script(
            *self.required_args,
            "--yes",
            use_env_override=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Environment: local", result.stdout)
        self.assertIn("tenant_admin", result.stdout)

    def test_database_not_at_alembic_head_fails_closed(self):
        result = self.run_script(
            *self.required_args,
            "--yes",
            env_updates={"FAKE_NOT_HEAD": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not at Alembic head", result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertNotIn("bootstrap-access", calls)

    def test_disabled_identity_is_rejected(self):
        result = self.run_script(
            *self.required_args,
            "--yes",
            env_updates={"FAKE_USER_STATUS": "disabled"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disabled or suspended", result.stderr)
        self.assertNotIn("bootstrap-access", self.log.read_text(encoding="utf-8"))

    def test_dry_run_precedes_confirmation_rejection(self):
        result = self.run_script(*self.required_args, input_text="no\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Running mandatory dry-run", result.stdout)
        self.assertIn("No changes applied", result.stdout)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("bootstrap-access", calls)
        self.assertIn("--dry-run", calls)
        self.assertNotIn("--confirm", calls)

    def test_bootstrap_and_idempotent_rerun(self):
        first = self.run_script(*self.required_args, "--yes")
        second = self.run_script(*self.required_args, "--yes")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("tenant_admin", second.stdout)
        self.assertIn("platform admin: no", second.stdout)
        self.assertGreaterEqual(
            self.log.read_text(encoding="utf-8").count("bootstrap-access"), 4
        )

    def test_optional_platform_admin_is_separate(self):
        result = self.run_script(
            *self.required_args,
            "--platform-admin",
            "--yes",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("platform admin: yes", result.stdout)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("grant-platform-admin", calls)
        self.assertIn("--expect-platform-admin", calls)

    def test_output_never_contains_environment_secrets(self):
        result = self.run_script(*self.required_args, "--yes")
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("never-print-openai-secret", combined)
        self.assertNotIn("never-print-google-secret", combined)
        self.assertNotIn("stable-provider-subject", combined)


if __name__ == "__main__":
    unittest.main()
