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
        result = subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, check=False, env={**__import__("os").environ, "VIDEO_8B_TARGET_COMMIT": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "VIDEO_8B_RELEASE_ID": "aaaaaaaaaaaa"})
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
            result = subprocess.run(["bash", "-c", f"source {SCRIPT}; {invocation}"], cwd=ROOT, capture_output=True, text=True, check=False, env={**__import__("os").environ, "VIDEO_8B_TARGET_COMMIT": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "VIDEO_8B_RELEASE_ID": "aaaaaaaaaaaa"})
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
    def test_rollback_finalizer_and_positive_orchestration_are_executable(self):
        environment = {**__import__("os").environ, "VIDEO_8B_TARGET_COMMIT": "a" * 40, "VIDEO_8B_RELEASE_ID": "a" * 12}
        before = subprocess.run(["bash", "-c", "source " + str(SCRIPT) + "; sudo() { :; }; CAM_DEPLOY=echo; SWITCHED=false; die before"], capture_output=True, text=True, env=environment)
        self.assertNotIn("rollback-release", before.stderr)
        after = subprocess.run(["bash", "-c", "source " + str(SCRIPT) + "; sudo() { echo \"$*\" >&2; }; CAM_DEPLOY=echo; SWITCHED=true; die after"], capture_output=True, text=True, env=environment)
        self.assertIn("rollback-release", after.stderr)
        command = f"""source {SCRIPT}
order=""
record() {{ order=\"$order,$1\"; }}
validate_inputs() {{ record validate; }}
require_production_host() {{ record host; }}
require_source_checkout() {{ record source; }}
verify_video_flags_off() {{ record flags; }}
require_native_runtime() {{ record runtime; }}
prepare_video_storage() {{ record storage; }}
require_elasticsearch() {{ record elasticsearch; }}
env_value() {{ printf prefix; }}
snapshot_image_aliases() {{ record snapshot; printf /tmp/image-aliases; }}
run_index_provisioning() {{ record index; }}
sudo() {{ record \"$2\"; }}
APP_ROOT=/tmp/video8b-test
main
printf '%s\n' \"$order\"
"""
        result = subprocess.run(["bash", "-c", command], cwd=ROOT, capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        order = result.stdout.strip()
        for item in ("host", "source", "flags", "runtime", "storage", "elasticsearch", "install-release", "check-config", "verify-alembic-head", "migrate", "seed", "index", "switch-release", "restart-api", "restart-worker", "verify-api", "verify-worker", "diagnostics"):
            self.assertIn(item, order)
        self.assertLess(order.index("migrate"), order.index("seed"))
        self.assertLess(order.index("seed"), order.index("index"))
        self.assertLess(order.index("index"), order.index("switch-release"))


if __name__ == "__main__":
    unittest.main()
