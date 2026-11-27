"""Shared creative settings are operator controlled and independent of worker identity."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from app.modules.muse_bridge import shared_video_policy as policy
from app.modules.muse_bridge.story_lite import lite_story_spec
from app.modules.muse_bridge.story_prompts import build_story_spec


class SharedVideoPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.directory.chmod(0o700)
        self.location = patch.object(policy, "_directory", return_value=self.directory)
        self.location.start()
        self.addCleanup(self.location.stop)

    def change(self, revision=1, **kwargs):
        return policy.ChangeRequest(expectedRevision=revision, changes=policy.CreativePatch(**kwargs))

    def story(self):
        return lite_story_spec(build_story_spec(title="Garden T-shirt", listing_key="4404376054",
                             artwork_sha256="a"*64, garment_code="C1717"))

    def test_default_and_deterministic_profiles(self):
        a = policy.active()
        self.assertEqual(a["revision"], 1)
        self.assertEqual(a["settings"]["castingVariantCount"], 480)
        self.assertEqual(policy.active(), a)
        self.assertFalse(policy.config()["verifiedReferenceFacePool"])
        self.assertEqual(len(set(policy._PROFILES)), 750)
        profiles = set()
        for i in range(1000):
            p = policy.casting_profile(a, str(i), "a"*64, "9:16", "hero")
            self.assertEqual(p, policy.casting_profile(a, str(i), "a"*64, "9:16", "hero"))
            profiles.add(p["profileId"])
        self.assertGreater(len(profiles), 380)

    def test_proposal_requires_operator_publish(self):
        before = policy.active()
        p = policy.propose("muse-01", self.change(castingVariantCount=600))
        self.assertEqual(p["status"], "PENDING_OPERATOR_APPROVAL")
        self.assertEqual(policy.active(), before)
        self.assertEqual(policy.propose("muse-01", self.change(castingVariantCount=600))["proposalId"], p["proposalId"])
        self.assertEqual(len(policy.registry()["proposals"]), 1)
        other = policy.propose("muse-02", self.change(expressionStyle="warm"))
        self.assertNotEqual(p["proposalId"], other["proposalId"])
        self.assertEqual(policy.active(), before)
        approved = policy.decide(policy.DecisionRequest(expectedRevision=1, action="approve", proposalId=p["proposalId"]))
        self.assertEqual(approved["active"]["revision"], 2)
        self.assertEqual(policy.active()["settings"]["castingVariantCount"], 600)
        self.assertEqual(policy.registry()["proposals"], [])
        self.assertNotEqual(before["sha256"], policy.active()["sha256"])

    def test_revision_and_rollback(self):
        before = policy.active()
        new = policy.publish(self.change(motionStyle="small_step"))["active"]
        self.assertEqual(new["revision"], 2)
        with self.assertRaises(HTTPException) as ctx:
            policy.publish(self.change(expressionStyle="calm"))
        self.assertEqual(ctx.exception.status_code, 409)
        back = policy.decide(policy.DecisionRequest(expectedRevision=2, action="rollback", rollbackRevision=1))["active"]
        self.assertEqual(back["revision"], 3)
        self.assertEqual(back["settings"], before["settings"])
        self.assertNotEqual(back["sha256"], before["sha256"])

    def test_reject_and_invalid_settings(self):
        p = policy.propose("muse-02", self.change(lightingStyle="existing_reference"))
        result = policy.decide(policy.DecisionRequest(expectedRevision=1, action="reject", proposalId=p["proposalId"]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(policy.active()["revision"], 1)
        for changes in ({"castingVariantCount": 999}, {"cooldownActive": False},
                        {"expressionStyle": "execute arbitrary command"}, {"castingVariantCount": "500"}):
            with self.assertRaises(ValidationError):
                policy.CreativePatch.model_validate(changes)

    def test_pinned_snapshot_survives_global_update(self):
        snapshot = policy.active()
        before = policy.apply_story(self.story(), snapshot, "4404376054", "a"*64)
        after_snapshot = policy.publish(self.change(expressionStyle="warm", castingVariantCount=600))["active"]
        after = policy.apply_story(self.story(), after_snapshot, "4404376054", "a"*64)
        pinned = policy.apply_story(self.story(), snapshot, "4404376054", "a"*64)
        self.assertEqual(before, pinned)
        self.assertEqual(after["sharedVideoPolicy"]["revision"], 2)
        self.assertIn("warm expression", after["aspectPlans"]["9:16"]["prompt"])
        self.assertIn("background", after["aspectPlans"]["9:16"]["prompt"])
        self.assertEqual(before["storyOutputContract"], after["storyOutputContract"])
        self.assertEqual(before["renderPolicy"], after["renderPolicy"])
        self.assertEqual(before["aspectPlans"]["9:16"]["heroSeed"], after["aspectPlans"]["9:16"]["heroSeed"])
        self.assertEqual(before["aspectPlans"]["9:16"]["heroRequestId"], after["aspectPlans"]["9:16"]["heroRequestId"])

    def test_registry_corruption_fails_closed(self):
        policy.publish(self.change(castingVariantCount=600))
        path = self.directory / "registry.json"
        path.write_text('{"invalid": true}')
        with self.assertRaises(HTTPException) as ctx:
            policy.active()
        self.assertEqual(ctx.exception.status_code, 503)

    def test_file_permissions(self):
        policy.publish(self.change(castingVariantCount=500))
        self.assertEqual((self.directory / "registry.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.directory / ".lock").stat().st_mode & 0o777, 0o600)

    def test_empty_and_same_setting(self):
        with self.assertRaises(HTTPException):
            policy.propose("muse-02", self.change())
        self.assertEqual(policy.publish(self.change(castingVariantCount=480))["status"], "ALREADY_ACTIVE")
        self.assertEqual(policy.active()["revision"], 1)

if __name__ == "__main__":
    unittest.main()
