"""Read-only, no-network tests for prefix-safe Cloudflare lifecycle changes."""
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from deploy.tools import r2_video_lifecycle_30d as lifecycle


class LifecyclePolicyTests(unittest.TestCase):
    def test_expiration_is_exactly_30_days_and_only_video_prefix(self):
        self.assertEqual(lifecycle.RULE["conditions"]["prefix"], "video-cache/")
        self.assertEqual(
            lifecycle.RULE["deleteObjectsTransition"]["condition"]["maxAge"],
            30 * 24 * 3600,
        )
        self.assertNotIn("etsy-images/", lifecycle.RULE["conditions"]["prefix"])

    def test_merge_keeps_unrelated_image_and_multipart_rules(self):
        other = [
            {"id": "etsy-preview-retention", "enabled": True,
             "conditions": {"prefix": "etsy-images/"},
             "deleteObjectsTransition": {"condition": {"type": "Age", "maxAge": 60 * 86400}}},
            {"id": "multipart", "enabled": True, "conditions": {"prefix": ""},
             "abortMultipartUploadsTransition": {"condition": {"type": "Age", "maxAge": 7 * 86400}}},
        ]
        merged, existing = lifecycle.merge_rules(other)
        self.assertFalse(existing)
        self.assertEqual(merged[:2], other)
        self.assertEqual(merged[-1], lifecycle.RULE)
        repeated, existed = lifecycle.merge_rules(merged)
        self.assertTrue(existed)
        self.assertEqual(repeated, merged)

    def test_rejects_conflicting_broad_rules_and_never_replaces_foreign_ids(self):
        conflicting = [
            {"id": "all-videos-60-days", "enabled": True,
             "conditions": {"prefix": "video-cache/"},
             "deleteObjectsTransition": {"condition": {"type": "Age", "maxAge": 60 * 86400}}},
        ]
        with self.assertRaises(lifecycle.LifecycleBlocked):
            lifecycle.merge_rules(conflicting)
        conflicting[0]["conditions"]["prefix"] = ""
        with self.assertRaises(lifecycle.LifecycleBlocked):
            lifecycle.merge_rules(conflicting)
        wrong = {**lifecycle.RULE, "conditions": {"prefix": "etsy-images/"}}
        with self.assertRaises(lifecycle.LifecycleBlocked):
            lifecycle.merge_rules([wrong])

    def test_apply_does_not_write_when_read_failed_or_token_missing(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "production.env"
            path.write_text("R2_BUCKET_NAME=creative-asset-video-cache\nR2_ACCOUNT_ID=" +
                            "0" * 32 + "\n")
            with patch.dict("os.environ", {}, clear=True), patch.object(lifecycle, "_api") as api:
                with self.assertRaises(lifecycle.LifecycleBlocked):
                    lifecycle.run("apply", path)
                api.assert_called_once()
            with patch.object(lifecycle, "_api", side_effect=lifecycle.LifecycleBlocked("cloudflare_api_http_403")):
                with self.assertRaises(lifecycle.LifecycleBlocked):
                    lifecycle.run("apply", path)

    def test_plan_needs_no_cloudflare_permissions(self):
        result = lifecycle.run("plan", Path("/no-such-file"))
        self.assertEqual(result["bucket"], "creative-asset-video-cache")
        self.assertEqual(result["rule"]["conditions"]["prefix"], "video-cache/")


if __name__ == "__main__":
    unittest.main()
