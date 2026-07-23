from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GATE_SCRIPT = ROOT / "scripts" / "production-release-gate.sh"


class ProductionReleaseGateTest(unittest.TestCase):
    def test_gate_waits_for_every_existing_ci_group(self):
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        gate = workflow["jobs"]["production-release-gate"]
        self.assertEqual(
            set(gate["needs"]),
            {
                "frontend",
                "api-unit",
                "postgres-integration",
                "elasticsearch-integration",
                "pipeline-e2e",
            },
        )
        self.assertIn("postgres", gate["services"])
        self.assertEqual(gate["services"]["postgres"]["image"], "postgres:16.4")

    def test_gate_script_has_valid_shell_syntax_and_required_runtime_checks(self):
        result = subprocess.run(
            ["bash", "-n", str(GATE_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        source = GATE_SCRIPT.read_text()
        for required in (
            "validate_production_env_file",
            "scan_frontend_dist",
            "docker build",
            "host.docker.internal",
            "python -m alembic current",
            "http://127.0.0.1:8000/live",
            "http://127.0.0.1:8000/ready",
            "http://127.0.0.1:8000/version",
            "stop -t 45 worker",
            "nginx -t",
        ):
            self.assertIn(required, source)

    def test_gate_environment_is_fail_closed(self):
        workflow = WORKFLOW.read_text()
        required = (
            "PERSISTENT_AUTH_ENABLED=true",
            "DEVELOPMENT_PERSONAL_TENANT_ENABLED=false",
            "AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED=false",
            "DATABASE_URL=postgresql+psycopg://cam_gate:cam_gate@host.docker.internal:5432/cam_release_gate",
            "PROCESSING_JOBS_ENABLED=false",
            "ELASTICSEARCH_V2_ENABLED=false",
        )
        for value in required:
            self.assertIn(value, workflow)
        self.assertNotIn("DATABASE_URL=sqlite", workflow)
        self.assertNotIn(
            "DATABASE_URL=postgresql+psycopg://cam_gate:cam_gate@127.0.0.1",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
