"""Read-only tests for Etsy image R2 lifecycle preservation and fail-closed policy."""
from __future__ import annotations

import unittest

from deploy.tools import r2_etsy_image_lifecycle_30d as target


class FakeR2:
    def __init__(self, rules):
        self.rules = [dict(v) for v in rules]
        self.writes = 0

    def get_bucket_lifecycle_configuration(self, *, Bucket):
        assert Bucket == target.BUCKET
        return {"Rules": [dict(v) for v in self.rules]}

    def put_bucket_lifecycle_configuration(self, *, Bucket, LifecycleConfiguration):
        assert Bucket == target.BUCKET
        self.rules = [dict(v) for v in LifecycleConfiguration["Rules"]]
        self.writes += 1


class ImageR2RetentionTests(unittest.TestCase):
    VIDEO = {
        "ID": "cam-video-cache-expire-30d",
        "Status": "Enabled",
        "Filter": {"Prefix": "video-cache/"},
        "Expiration": {"Days": 30},
    }
    MULTIPART = {
        "ID": "abort-incomplete",
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
    }

    def test_only_exact_image_prefix_at_30_days(self):
        self.assertEqual(target.RULE["Filter"]["Prefix"], "etsy-images/v1/")
        self.assertEqual(target.RULE["Expiration"], {"Days": 30})
        self.assertNotIn("video-cache", target.RULE["Filter"]["Prefix"])

    def test_check_never_changes_bucket_and_apply_keeps_all_other_rules(self):
        client = FakeR2([self.VIDEO, self.MULTIPART])
        checked = target.operate("check", client=client)
        self.assertFalse(checked["configured"])
        self.assertEqual(client.writes, 0)
        applied = target.operate("apply", client=client)
        self.assertTrue(applied["configured"])
        self.assertTrue(applied["changed"])
        self.assertEqual(client.writes, 1)
        self.assertEqual(client.rules[:2], [self.VIDEO, self.MULTIPART])
        self.assertEqual(client.rules[-1], target.RULE)
        again = target.operate("apply", client=client)
        self.assertFalse(again["changed"])
        self.assertEqual(client.writes, 1)

    def test_conflicting_global_or_overlapping_retention_is_rejected(self):
        for prefix in ("", "etsy-images/", "etsy-images/v1/", "etsy-images/v1/foo/"):
            bad = {
                "ID": "unrelated-delete",
                "Status": "Enabled",
                "Filter": {"Prefix": prefix},
                "Expiration": {"Days": 1},
            }
            with self.assertRaises(target.RetentionBlocked):
                target.operate("apply", client=FakeR2([self.VIDEO, bad]))
        with self.assertRaises(target.RetentionBlocked):
            target.validate_existing([{
                "ID": target.RULE_ID,
                "Status": "Enabled",
                "Filter": {"Prefix": target.PREFIX},
                "Expiration": {"Days": 1},
            }])

    def test_unknown_existing_delete_filter_and_duplicates_fail_closed(self):
        bad = {"ID": "unknown", "Status": "Enabled",
               "Filter": {"And": {"Tags": [{"Key": "x", "Value": "y"}]}},
               "Expiration": {"Days": 10}}
        with self.assertRaises(target.RetentionBlocked):
            target.operate("apply", client=FakeR2([bad]))
        with self.assertRaises(target.RetentionBlocked):
            target.operate("apply", client=FakeR2([self.VIDEO, self.VIDEO]))

    def test_plan_does_not_need_credentials(self):
        result = target.operate("plan")
        self.assertEqual(result["prefix"], "etsy-images/v1/")
        self.assertEqual(result["days"], 30)

    def test_bad_bucket_and_endpoint_never_create_client(self):
        from unittest.mock import patch
        with self.assertRaises(target.RetentionBlocked), patch("boto3.client") as api:
            target.make_client({"R2_BUCKET_NAME": "private-masters"})
            api.assert_not_called()
        with self.assertRaises(target.RetentionBlocked), patch("boto3.client") as api:
            target.make_client({
                "R2_BUCKET_NAME": target.BUCKET,
                "R2_ENDPOINT": "http://127.0.0.1:1337",
            })
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
