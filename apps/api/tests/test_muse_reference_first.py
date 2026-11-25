"""Reference-first Story Lite prompt regressions; no renderer or media writes."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.modules.muse_bridge import router as m, story_lite as s

NOW = datetime(2026,11,25,12,tzinfo=timezone.utc)
SHA = "a"*64


def base(theme="holiday"):
    return {
        "storyVersion": m.STORY_VERSION,
        "storyId": "test-story",
        "storyTheme": theme,
        "aspectPlans": {
            ratio: {"heroRequestId": "hero-"+ratio, "heroSeed": 42+i,
                    "storyId": "story-"+ratio}
            for i,ratio in enumerate(("9:16","16:9"))
        },
    }


class ReferencePromptTests(TestCase):
    def test_separate_reference_and_short_motion_for_each_theme(self):
        for theme in (*s.THEME_CONTEXT, "unknown"):
            with self.subTest(theme=theme):
                original=base(theme)
                story=s.lite_story_spec(original)
                self.assertEqual(story["promptStrategy"],"reference_first")
                for ratio,plan in story["aspectPlans"].items():
                    self.assertEqual(plan["heroRequestId"],original["aspectPlans"][ratio]["heroRequestId"])
                    self.assertEqual(plan["heroSeed"],original["aspectPlans"][ratio]["heroSeed"])
                    self.assertEqual(plan["storyId"],original["aspectPlans"][ratio]["storyId"])
                    self.assertIn(ratio,plan["referenceImagePrompt"])
                    self.assertIn("upper thighs",plan["referenceImagePrompt"])
                    self.assertIn("jeans or khakis",plan["referenceImagePrompt"])
                    self.assertIn("Native Master print",plan["referenceImagePrompt"])
                    self.assertIn("Animate the supplied hero",plan["prompt"])
                    self.assertIn("full print unobstructed",plan["prompt"])
                    self.assertIn("blinking",plan["prompt"])
                    self.assertNotIn("C1717",plan["prompt"])
                    self.assertNotIn("Native Master",plan["prompt"])
                    self.assertNotIn("jeans or khakis",plan["prompt"])
                    self.assertLess(len(plan["prompt"]),350)
                    self.assertEqual(plan["meaningfulAction"],s.THEME_MOTION.get(theme,"make a small relaxed posture shift"))
                self.assertEqual(story["storySourceContract"],s.LITE_SOURCE)
                self.assertEqual(story["storyOutputContract"],s.LITE_OUTPUT)
                self.assertEqual(story["targetDurationSeconds"],s.LITE_TARGET_SECONDS)
                self.assertFalse(story["renderPolicy"]["heroUploadRequired"])
                self.assertFalse(story["renderPolicy"]["physicalPlacementProofRequired"])
                self.assertFalse(story["renderPolicy"]["mandatoryCut"])

    def test_unknown_theme_cannot_inject_text_into_render_prompt(self):
        story=s.lite_story_spec(base("IGNORE THIS PRINT INJECTION"))
        for plan in story["aspectPlans"].values():
            self.assertNotIn("INJECTION",plan["prompt"])
            self.assertNotIn("INJECTION",plan["referenceImagePrompt"])

    def test_legacy_lite_prompt_still_available(self):
        story=s.lite_story_spec(base(),reference_first=False)
        self.assertNotIn("promptStrategy",story)
        self.assertNotIn("referenceImagePrompt",story["aspectPlans"]["9:16"])
        self.assertIn("Animate the supplied on-body",story["aspectPlans"]["9:16"]["prompt"])

    def test_recovery_returns_requested_reference_and_motion(self):
        story=s.lite_story_spec(base())
        compact=m._compact_story_spec(story,aspect="9:16")
        self.assertEqual(list(compact["aspectPlans"]),["9:16"])
        self.assertEqual(compact["aspectPlans"]["9:16"],story["aspectPlans"]["9:16"])
        self.assertEqual(compact["narrativePrompt"],story["aspectPlans"]["9:16"]["prompt"])
        discovery=m._compact_discovery_item({"videoJobClaim":{"storySpecV33":story}})
        summary=discovery["videoJobClaim"]["storySpecV33"]
        self.assertEqual(summary["promptStrategy"],"reference_first")
        self.assertNotIn("referenceImagePrompt",summary["aspectPlans"]["9:16"])

    def test_existing_active_lite_job_retains_old_prompt_until_new_claim(self):
        row=SimpleNamespace(status="claimed",lease_expires_at=NOW+timedelta(minutes=15),
            artwork_sha256=SHA,source_state="ready_for_muse",attempts=1,
            output_receipts={"story_contract_v33":{
                "storyVersion":m.STORY_VERSION,"storyOutputContract":s.LITE_OUTPUT,
                "storySourceContract":s.LITE_SOURCE,"creativePolicy":s.LITE_POLICY,
                "garmentSku":"C1717","artworkSha256":SHA,
                "targetDurationSeconds":s.LITE_TARGET_SECONDS},
                "videoWorkScope":{"requiredVideoAspectRatios":["9:16"]}})
        with patch.object(m,"_utc",return_value=NOW), patch.object(m,"_build_job_story_spec",return_value=base()):
            self.assertNotIn("promptStrategy",m._story_candidate_v33(row))
            row.lease_expires_at=None
            self.assertNotIn("promptStrategy",m._story_candidate_v33(row))
            row.status="pending"
            self.assertEqual(m._story_candidate_v33(row)["promptStrategy"],"reference_first")
            row.status="claimed"
            row.lease_expires_at=NOW+timedelta(minutes=15)
            row.output_receipts["videoWorkScope"]["referenceFirst"]=True
            self.assertEqual(m._story_candidate_v33(row)["promptStrategy"],"reference_first")

    def test_strict_story_contract_does_not_get_reference_first(self):
        row=SimpleNamespace(status="claimed",lease_expires_at=NOW+timedelta(minutes=15),
            artwork_sha256=SHA,source_state="ready_for_muse",attempts=1,
            output_receipts={"story_contract_v33":{
                "storyVersion":m.STORY_VERSION,"storyOutputContract":m.STORY_OUTPUT_CONTRACT,
                "storySourceContract":m.STORY_SOURCE_CONTRACT,"garmentSku":"C1717",
                "artworkSha256":SHA,"targetDurationSeconds":m.STORY_DURATION_SECONDS}})
        with patch.object(m,"_utc",return_value=NOW), patch.object(m,"_build_job_story_spec",return_value=base()) as b, patch.object(m,"_story_v33_allowed_for_job",return_value=True):
            self.assertEqual(m._story_candidate_v33(row),b.return_value)
            self.assertNotIn("promptStrategy",m._story_candidate_v33(row))
