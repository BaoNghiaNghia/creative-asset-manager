from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deploy.tools.r2_video_worker_rollout import (
    BINDING_NAME,
    SECRET_NAME,
    RolloutConfigError,
    build_config,
    rollout_plan,
    validate_config,
)


class R2VideoWorkerRolloutTest(unittest.TestCase):
    def config(self, **updates):
        values = {
            "worker_name": "cam-r2-original-video",
            "bucket_name": "cam-private-video",
            "media_host": "media.example.com",
            "max_ttl_seconds": 600,
        }
        values.update(updates)
        return build_config(**values)

    def test_rendered_config_is_private_custom_domain_and_secret_name_only(self):
        config = self.config()
        summary = validate_config(config)
        self.assertFalse(config["workers_dev"])
        self.assertEqual(
            config["routes"],
            [{"pattern": "media.example.com", "custom_domain": True}],
        )
        self.assertEqual(
            config["r2_buckets"],
            [{"binding": BINDING_NAME, "bucket_name": "cam-private-video"}],
        )
        self.assertEqual(config["secrets"]["required"], [SECRET_NAME])
        serialized = json.dumps(config)
        self.assertNotIn("secret-value", serialized)
        self.assertEqual(summary["max_ttl_seconds"], 600)
        self.assertEqual(summary["endpoint_mode"], "custom_domain")

    def test_rendered_workers_dev_config_has_no_custom_domain_route(self):
        config = build_config(
            worker_name="cam-r2-original-video",
            bucket_name="cam-private-video",
            workers_dev=True,
            max_ttl_seconds=600,
        )
        summary = validate_config(config)
        self.assertTrue(config["workers_dev"])
        self.assertNotIn("routes", config)
        self.assertEqual(summary["endpoint_mode"], "workers_dev")
        self.assertTrue(summary["workers_dev"])
        self.assertIsNone(summary["media_host"])
        self.assertEqual(
            config["r2_buckets"],
            [{"binding": BINDING_NAME, "bucket_name": "cam-private-video"}],
        )

    def test_workers_dev_and_custom_domain_modes_are_mutually_exclusive(self):
        with self.assertRaises(RolloutConfigError):
            build_config(
                worker_name="cam-r2-original-video",
                bucket_name="cam-private-video",
                media_host="media.example.com",
                workers_dev=True,
            )
        with self.assertRaises(RolloutConfigError):
            build_config(
                worker_name="cam-r2-original-video",
                bucket_name="cam-private-video",
            )

    def test_public_cloudflare_and_raw_r2_hosts_are_rejected(self):
        for host in (
            "cam.workers.dev",
            "bucket.r2.dev",
            "account.r2.cloudflarestorage.com",
        ):
            with self.subTest(host=host):
                with self.assertRaises(RolloutConfigError):
                    self.config(media_host=host)

    def test_invalid_worker_bucket_ttl_and_route_fail_closed(self):
        for updates in (
            {"worker_name": "../worker"},
            {"bucket_name": "UPPER"},
            {"max_ttl_seconds": 0},
            {"max_ttl_seconds": 3601},
        ):
            with self.subTest(updates=updates):
                with self.assertRaises(RolloutConfigError):
                    self.config(**updates)

        config = self.config()
        config["workers_dev"] = True
        with self.assertRaises(RolloutConfigError):
            validate_config(config)

    def test_rollout_plan_does_not_execute_or_contain_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrangler.production.json"
            path.write_text(json.dumps(self.config()), encoding="utf-8")
            plan = rollout_plan(path, release_tag="phase4d-test")
        self.assertFalse(plan["secret_values_in_plan"])
        self.assertTrue(plan["application_runtime_must_remain_off"])
        serialized = json.dumps(plan)
        self.assertIn("versions upload", serialized)
        self.assertIn("versions deploy", serialized)
        self.assertIn("rollback", serialized)
        self.assertNotIn("secret-value", serialized)
        remote = [step for step in plan["steps"] if step["mutates_remote"]]
        self.assertTrue(remote)
        self.assertTrue(all("commands" in step for step in remote))


    def test_workers_dev_rollout_plan_skips_custom_domain_trigger_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrangler.production.json"
            path.write_text(
                json.dumps(build_config(
                    worker_name="cam-r2-original-video",
                    bucket_name="cam-private-video",
                    workers_dev=True,
                    max_ttl_seconds=600,
                )),
                encoding="utf-8",
            )
            plan = rollout_plan(path, release_tag="workers-dev-test")
        self.assertTrue(plan["config"]["workers_dev"])
        stages = [step["stage"] for step in plan["steps"]]
        self.assertNotIn("route_review", stages)
        self.assertIn("deployment", stages)


if __name__ == "__main__":
    unittest.main()
