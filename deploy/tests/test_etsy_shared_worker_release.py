"""No-network rollout regression tests for the existing Cloudflare R2 Worker."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from deploy.tools import etsy_shared_worker_release as release
from deploy.tools.r2_video_worker_rollout import build_config


def values():
    return {
        "R2_BUCKET_NAME": "creative-asset-video-cache",
        "R2_VIDEO_MEDIA_BASE_URL": "https://cam-r2-original-video.example.workers.dev",
        "R2_VIDEO_MEDIA_SIGNING_SECRET": "test-only-secret-no-production-use",
        "R2_VIDEO_MEDIA_TICKET_TTL_SECONDS": "540",
        "R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV": "true",
        "CAM_ETSY_IMAGE_SHARED_R2_ENABLED": "true",
        "CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS": "boostgame",
        "CAM_ETSY_IMAGE_SHARED_WORKER_ENABLED": "false",
    }


def worker_config(**changes):
    config = build_config(
        worker_name="cam-r2-original-video",
        bucket_name="creative-asset-video-cache",
        workers_dev=True,
        max_ttl_seconds=600,
    )
    config.update(changes)
    return config


def edge_info(ready=False):
    return {
        "edge_ready": ready,
        "csp_allows_worker": True,
        "worker_signed_head": (
            "signed_image_route_verified" if ready else
            "existing_worker_still_video_only_or_signature_rejected"
        ),
    }


class ReleaseGateTests(unittest.TestCase):
    def test_only_existing_worker_bucket_and_video_ttl_accepted(self):
        check = release.gate(values(), worker_config())
        self.assertEqual(check["bucket"], "creative-asset-video-cache")
        self.assertEqual(check["edge_flag"], "off")
        for key, altered in (
            ("R2_BUCKET_NAME", "other-bucket"),
            ("R2_VIDEO_MEDIA_TICKET_TTL_SECONDS", "650"),
            ("R2_VIDEO_MEDIA_BASE_URL", "https://another-worker.example.workers.dev"),
            ("CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS", "boostgame,other"),
            ("CAM_ETSY_IMAGE_SHARED_WORKER_ENABLED", "true"),
            ("R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV", "false"),
        ):
            changed = values()
            changed[key] = altered
            with self.subTest(key=key), self.assertRaises(release.ReleaseBlocked):
                release.gate(changed, worker_config())
        with self.assertRaises(release.ReleaseBlocked):
            release.gate(values(), worker_config(name="wrong-worker"))
        with self.assertRaises(release.ReleaseBlocked):
            release.gate(values(), worker_config(
                r2_buckets=[{"binding": "VIDEO_CACHE_BUCKET", "bucket_name": "another-bucket"}]
            ))


    def test_synthetic_video_head_checks_missing_object_without_uploads(self):
        from urllib.error import HTTPError
        from urllib.parse import parse_qs, urlsplit
        from hashlib import sha256
        import base64
        import hmac

        captured = []
        def missing(request, timeout):
            captured.append(request)
            raise HTTPError(request.full_url, 404, "object absent", {}, None)
        with patch.object(release, "urlopen", side_effect=missing):
            self.assertEqual(release.probe_video_head(values()), 404)
        request = captured[0]
        self.assertEqual(request.get_method(), "HEAD")
        parsed = urlsplit(request.full_url)
        self.assertEqual(parsed.path,
                         "/video-cache/boostgame/" + "0" * 64 + "/original")
        query = parse_qs(parsed.query)
        message = f"v1\nread\n{parsed.path}\n{query['exp'][0]}".encode()
        expected = base64.urlsafe_b64encode(
            hmac.new(values()["R2_VIDEO_MEDIA_SIGNING_SECRET"].encode(),
                     message, sha256).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(query["sig"], [expected])
        with patch.object(release, "urlopen", side_effect=HTTPError(
            request.full_url, 403, "invalid", {}, None)):
            self.assertEqual(release.probe_video_head(values()), 403)

    def call_main(self, action, *, report, runner):
        with TemporaryDirectory() as folder:
            env = Path(folder) / "production.env"
            config = Path(folder) / "wrangler.production.json"
            env.write_text("\n".join(f"{k}={v}" for k, v in values().items()) + "\n")
            config.write_text(json.dumps(worker_config()))
            output = StringIO()
            with (
                patch.object(release, "PRODUCTION_ENV", env),
                patch.object(release, "PRODUCTION_CONFIG", config),
                patch.object(release, "probe", side_effect=report),
                patch.object(release, "probe_video_head", return_value=404),
                patch.object(release, "run", side_effect=runner),
                patch.object(release, "time") as timer,
                patch("sys.argv", ["worker_release", action]),
                patch.object(release.Path, "is_file", return_value=True),
                redirect_stdout(output),
            ):
                code = release.main()
            return code, json.loads(output.getvalue()), timer.sleep.call_count

    def test_check_never_calls_wrangler(self):
        calls = []
        code, result, _ = self.call_main(
            "check", report=[edge_info()], runner=lambda argv: calls.append(argv)
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["release_gate"], "ready_for_worker_auth")
        self.assertEqual(calls, [])

    def test_missing_cloudflare_auth_stops_before_deployment(self):
        calls = []
        def runner(argv):
            calls.append(argv)
            return False
        code, result, _ = self.call_main(
            "deploy", report=[edge_info()], runner=runner,
        )
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "cloudflare_auth_unavailable")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], "whoami")

    def test_deploy_checks_existing_worker_and_strict_keep_vars(self):
        calls = []
        def runner(argv):
            calls.append(argv)
            return True
        code, result, sleep_count = self.call_main(
            "deploy", report=[edge_info(), edge_info(ready=True)], runner=runner,
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["release_gate"],
                         "worker_deployed_signed_image_route_verified")
        self.assertEqual(sleep_count, 0)
        self.assertTrue(any(cmd[1:3] == ["deployments", "list"] for cmd in calls))
        deploy = [cmd for cmd in calls if "deploy" in cmd and "--dry-run" not in cmd]
        self.assertEqual(len(deploy), 1)
        self.assertIn("--strict", deploy[0])
        self.assertIn("--keep-vars", deploy[0])
        self.assertFalse(any("rollback" in cmd for cmd in calls))

    def test_postdeploy_probe_failure_fails_closed_and_rolls_back(self):
        calls = []
        def runner(argv):
            calls.append(argv)
            return True
        code, result, sleep_count = self.call_main(
            "deploy", report=[edge_info()] * 4, runner=runner,
        )
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"],
                         "post_deploy_probe_failed_rollback_succeeded")
        self.assertEqual(sleep_count, 2)
        self.assertTrue(any("rollback" in cmd for cmd in calls))


if __name__ == "__main__":
    unittest.main()
