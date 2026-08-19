from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "video_8b_production_deploy.sh"
HANDOFF = ROOT / "docs" / "operations" / "VIDEO_8B_PRODUCTION_DEPLOYMENT.md"

class Video8BProductionDeployTest(unittest.TestCase):
    def test_script_is_syntax_valid_and_fail_closed_on_a_non_production_host(self):
        syntax = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        result = subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, check=False, env={**__import__("os").environ, "VIDEO_8B_TARGET_COMMIT": "expected", "VIDEO_8B_RELEASE_ID": "expected"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing non-production host", result.stderr)

    def test_fail_closed_helpers_reject_wrong_source_and_unsafe_preconditions(self):
        cases = (
            ("git() { if [ \"$1\" = status ]; then printf \"\\n\"; else printf bad; fi; }; require_source_checkout", "reviewed VIDEO-8B commit"),
            ("git() { if [ \"$1\" = status ]; then printf M; else printf \"%s\\n\" \"125ead34f1c331c665ebc2c46849b961616a1117\"; fi; }; require_source_checkout", "source checkout is dirty"),
            ("env_value() { printf true; }; verify_video_flags_off", "required production setting is not safe"),
            ("command() { if [ \"$2\" = ffmpeg ]; then return 1; fi; builtin command \"$@\"; }; require_native_runtime", "ffmpeg is unavailable"),
            ("curl() { return 1; }; require_elasticsearch", "Elasticsearch is unavailable"),
        )
        for invocation, message in cases:
            result = subprocess.run(["bash", "-c", f"source {SCRIPT}; {invocation}"], cwd=ROOT, capture_output=True, text=True, check=False, env={**__import__("os").environ, "VIDEO_8B_TARGET_COMMIT": "expected", "VIDEO_8B_RELEASE_ID": "expected"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)

    def test_script_contains_all_fail_closed_preflight_guards(self):
        source = SCRIPT.read_text()
        for required in (
            "creative-asset-manager-api", "creative-asset-manager-worker",
            "/etc/creative-asset-manager/production.env",
            "git status --porcelain",
            "VIDEO_8B_TARGET_COMMIT", "VIDEO_8B_RELEASE_ID",
            "VIDEO_SEARCH_ENABLED false", "VIDEO_ANALYSIS_ENABLED false",
            "VIDEO_PROXY_ENABLED false", "command -v ffmpeg", "command -v ffprobe",
            "MIN_FREE_BYTES=1567108864", "http://127.0.0.1:9200/_cluster/health",
            "install-release", "verify-alembic-head", "rollback-release",
        ):
            self.assertIn(required, source)

    def test_handoff_records_the_reviewed_target_and_video_remains_off(self):
        text = HANDOFF.read_text()
        for required in (
            "VIDEO_8B_TARGET_COMMIT", "VIDEO_8B_RELEASE_ID",
            "VIDEO_SEARCH_ENABLED=false", "VIDEO_ANALYSIS_ENABLED=false",
            "VIDEO_PROXY_ENABLED=false", "VIDEO-8C", "rollback-release",
        ):
            self.assertIn(required, text)

if __name__ == "__main__":
    unittest.main()
