"""Mandatory real-action mini-story and measured C1717 print-source contract.

For new jobs only. Old V3.1 claims/uploads remain untouched.
A worker-submitted geometry proof is NOT an independent image QA approval.
"""
from __future__ import annotations

from .story_prompts import build_story_spec

STORY_ACTION_VERSION = "CAM_C1717_12S_REAL_ACTION_V1"
PLACEMENT_PROOF_VERSION = "CAM_C1717_150MM_SOURCE_MEASUREMENT_V1"
STORY_ACTION_MIN_SECONDS = 6
PLACEMENT_TARGET_MM = 150
PLACEMENT_TOLERANCE_MM = 5


def action_plan(*, title: str, listing_key: str, artwork_sha256: str) -> dict:
    base = build_story_spec(
        title=title, listing_key=listing_key,
        artwork_sha256=artwork_sha256, garment_code="C1717",
    )
    aspect_prompts = {}
    for ratio, plan in base["aspectPlans"].items():
        act_a = plan["shotA"]["action"]
        act_b = plan["shotB"]["action"]
        meaningful = plan["meaningfulAction"]
        aspect_prompts[ratio] = (
            "CAM-C1717 REAL-ACTION 12s VIDEO. This is a mini-story, NOT a "
            "standing person turning their head, rocking back and forth, "
            "sighing or only breathing. "
            f"Setting: {plan['sceneRequiresApprovedStartFrame']}. "
            "First generate a PLAIN/UNPRINTED C1717 short-sleeve T-SHIRT "
            "starting photo, with no AI-generated lettering. On its measured "
            "shirt front, let CAM composite the exact approved Native Master PNG "
            "at a CALCULATED 150mm collar-to-ink distance before video rendering. "
            "The FIRST ink is 150mm below the BOTTOM EDGE of the neck collar rib "
            "along the physical shirt front; never treat camera-pixel distance "
            "as centimeters without a shirt-size or measured plane calibration. "
            "If the blank shirt lacks a trustworthy physical fabric-plane "
            "reference, STOP before compositing or rendering and report "
            "PLACEMENT_UNVERIFIED. Never change a guessed millimeter scale "
            "or return a synthetic preflight to force PASS. "
            "12 seconds in two GENUINE moving 6-second takes, with one motivated "
            "edit at 6s; same person, shirt, print, setting and lighting. "
            f"0-3s: {act_a} "
            f"3-9s ACTION: {meaningful} "
            f"9-12s PAYOFF: {act_b} "
            "The actor must PHYSICALLY TRAVEL a believable forward route or "
            "genuinely stand up, open an existing door or cross a visible space. "
            "No stationary chest-up pose, back-and-forth neck turn, shoulder "
            "rock, inhale/exhale, forced sigh, camera-only pan/zoom, frozen "
            "frames, duplicated motion, reverse, slow-speed padding, invented "
            "props or moving backgrounds around a frozen torso. "
            "Film with the approved C1717 hem, neckline, original flat-ink "
            "print and natural fabric movement readable throughout. "
            "Check beginning/middle/end source frames and full MP4 against "
            "the C1717/150mm Native Master source BEFORE upload; if the print "
            "drifts closer to the collar at any time, HOLD and do not upload."
        )
    return {
        "policyVersion": STORY_ACTION_VERSION,
        "placementPolicyVersion": PLACEMENT_PROOF_VERSION,
        "storyId": base["storyId"],
        "storyTheme": base["storyTheme"],
        "storyTitle": base["storyTitle"],
        "approvedGarment": "Comfort Colors C1717 SHORT-SLEEVE crew-neck T-SHIRT",
        "artworkSha256": artwork_sha256,
        "targetDurationSeconds": 12,
        "nativeTakeMaxSeconds": 10,
        "requiredTakeCount": 2,
        "takeUseSeconds": [6, 6],
        "storyBeats": base["beats"],
        "actionPlansByAspect": {
            ratio: {
                "scene": plan["sceneRequiresApprovedStartFrame"],
                "shotA": plan["shotA"],
                "meaningfulAction": plan["meaningfulAction"],
                "shotB": plan["shotB"],
                "prompt": aspect_prompts[ratio],
            }
            for ratio, plan in base["aspectPlans"].items()
        },
        "disallowedMotion": [
            "static person with head turning left-right or slow sigh",
            "breathing, blinks or camera motion as the only changes",
            "stepping in place without actual displacement",
            "two takes showing unrelated subjects, print or scenes",
            "reversed/looped/frozen/padded video",
        ],
        "requireRealVisibleActionSeconds": STORY_ACTION_MIN_SECONDS,
        "sourceHero": {
            "garmentSku": "C1717",
            "artworkSha256": artwork_sha256,
            "firstInkBelowLowerCollarMm": PLACEMENT_TARGET_MM,
            "placementMeasurementToleranceMm": PLACEMENT_TOLERANCE_MM,
            "requiresFrontOnGarmentCalibration": True,
            "approvedCalibrationMethods": [
                "measured_c1717_shirt_size_template",
                "measured_reference_on_same_fabric_plane",
            ],
            "sourceMustBeC1717": True,
            "noEtsySweatshirtHero": True,
            "noUncalibratedPixelToCmAssumption": True,
            "constructionMode": "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1",
            "plainNativeRatioHeroBeforeApplyingPrint": True,
            "noGenerativeAiArtOrLettering": True,
        },
        "preRenderGate": (
            "Claim acknowledgement + source hero SHA + worker geometry "
            "measurement PASS must be recorded before uploading each ratio; "
            "unmeasurable print location = PLACEMENT_UNVERIFIED_HOLD."
        ),
        "videoVisualQa": (
            "Check genuine physical action during 3-9s, coherent storyline "
            "through the cut and same 150mm print on C1717 in every sampled "
            "frame. Worker measurement is provisional, independent content "
            "review remains required."
        ),
        "uploadRequiresMeasuredSourcePreflight": True,
        "independentVideoVisualQaPendingAfterUpload": True,
    }