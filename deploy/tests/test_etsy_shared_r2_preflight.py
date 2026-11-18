"""Safety regression tests for the read-only shared-R2 image edge preflight."""
from __future__ import annotations

from email.message import Message
from urllib.error import HTTPError
import unittest
from unittest.mock import patch

from deploy.tools import etsy_shared_r2_preflight as edge


ORIGIN = "https://cam-r2-original-video.example.workers.dev"
APP = "https://creative-assets.example.test"


class Response:
    def __init__(self, status=200, csp=""):
        self.status = status
        self.headers = Message()
        if csp:
            self.headers["Content-Security-Policy"] = csp

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def values():
    return {
        "R2_ENDPOINT": "https://some-account.r2.cloudflarestorage.com",
        "R2_BUCKET_NAME": "creative-asset-video-cache",
        "R2_ACCESS_KEY_ID": "not-a-real-id",
        "R2_SECRET_ACCESS_KEY": "not-a-real-secret",
        "R2_VIDEO_MEDIA_BASE_URL": ORIGIN,
        "R2_VIDEO_MEDIA_SIGNING_SECRET": "A_secure-Video-R2-secret-" + "Ab3Dxy" * 12,
        "PUBLIC_APP_URL": APP,
        "CAM_ETSY_IMAGE_SHARED_R2_ENABLED": "true",
        "CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS": "boostgame",
        "CAM_ETSY_IMAGE_SHARED_WORKER_ENABLED": "false",
    }


def fake_http(csp, worker_status=404):
    def send(request, timeout=None):
        if request.full_url.startswith(APP):
            return Response(csp=csp)
        raise HTTPError(request.full_url, worker_status, "synthetic", {}, None)
    return send


class SharedR2PreflightTest(unittest.TestCase):
    def test_fails_if_csp_allows_worker_only_as_media(self):
        policy = "default-src 'self'; img-src 'self' data:; media-src " + ORIGIN
        with patch.object(edge, "urlopen", fake_http(policy)):
            report = edge.probe(values(), do_network=True)
        self.assertEqual(report["worker_signed_head"], "signed_image_route_verified")
        self.assertFalse(report["csp_allows_worker"])
        self.assertFalse(report["edge_ready"])

    def test_valid_signed_absent_object_and_exact_csp_allows_edge(self):
        policy = "default-src 'self'; img-src 'self' " + ORIGIN + "; media-src " + ORIGIN
        with patch.object(edge, "urlopen", fake_http(policy)):
            report = edge.probe(values(), do_network=True)
        self.assertTrue(report["csp_allows_worker"])
        self.assertTrue(report["edge_ready"])

    def test_rejects_wildcards_prefix_and_unready_worker(self):
        for source in ("https://*.workers.dev", ORIGIN + ".other", "'self'"):
            policy = "img-src " + source + "; media-src " + ORIGIN
            with self.subTest(source=source), patch.object(
                edge, "urlopen", fake_http(policy)
            ):
                report = edge.probe(values(), do_network=True)
            self.assertFalse(report["edge_ready"])
        policy = "img-src " + ORIGIN
        with patch.object(edge, "urlopen", fake_http(policy, worker_status=403)):
            self.assertFalse(edge.probe(values(), do_network=True)["edge_ready"])

    def test_canary_and_shared_cache_are_required(self):
        policy = "img-src " + ORIGIN
        for k, val in (
            ("CAM_ETSY_IMAGE_SHARED_R2_ENABLED", "false"),
            ("CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS", "other-tenant"),
        ):
            configured = values()
            configured[k] = val
            with self.subTest(setting=k), patch.object(
                edge, "urlopen", fake_http(policy)
            ):
                self.assertFalse(edge.probe(configured, do_network=True)["edge_ready"])

    def test_invalid_media_origin_rejected_without_network(self):
        configured = values()
        configured["R2_VIDEO_MEDIA_BASE_URL"] = ORIGIN + "/not-the-worker"
        with patch.object(edge, "urlopen") as mocked:
            report = edge.probe(configured, do_network=True)
        self.assertFalse(report["edge_ready"])
        self.assertEqual(report["worker_signed_head"], "invalid_origin")
        mocked.assert_not_called()

    def test_csp_requires_exact_origin_in_all_headers(self):
        self.assertFalse(edge._policy_allows_worker("img-src *.workers.dev", ORIGIN))
        self.assertFalse(edge._policy_allows_worker("default-src *", ORIGIN))
        self.assertTrue(edge._policy_allows_worker("img-src 'self' " + ORIGIN, ORIGIN))


if __name__ == "__main__":
    unittest.main()
