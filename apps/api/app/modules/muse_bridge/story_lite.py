"""Throughput-first V3.3: one native clip, a simple event, stable character.

No renderer is called here. Visual content facts are not certified by MP4 storage.
"""
from __future__ import annotations

LITE_POLICY = "CAM_STORY_CHARACTER_LITE_V1"
LITE_OUTPUT = "CAM_STORY_NATIVE_FLEX_V1"
LITE_SOURCE = "CAM_STORY_CHARACTER_SOURCE_V1"
LITE_TARGET_SECONDS = 6
LITE_DURATION_BOUNDS = (5.0, 12.5)

# Keep motion low-complexity and grounded in the existing source.
# Never require a prop, another person, a location change or a hand action.
THEME_CONTEXT = {
    "holiday": "a relaxed festive everyday moment",
    "outdoors": "a relaxed moment outdoors",
    "ride": "a casual moment before or after a ride",
    "gaming": "a relaxed gaming-themed lifestyle moment",
    "sports": "a relaxed sports-day moment",
    "birthday": "a casual celebration moment",
    "pets": "a friendly everyday moment",
    "coffee": "a relaxed cafe or morning moment",
    "family": "a warm everyday moment",
    "romance": "a warm, affectionate everyday moment",
    "retro": "a relaxed retro-inspired lifestyle moment",
    "humor": "a lighthearted everyday moment",
}
THEME_INTENT = {
    "holiday": "Notice a festive detail already present in the scene and enjoy the moment.",
    "outdoors": "Pause to take in the surroundings after a short step.",
    "ride": "Settle into a relaxed stance before continuing the day.",
    "gaming": "Enjoy a brief break from the activity already suggested by the setting.",
    "sports": "Take a short recovery moment after an everyday activity.",
    "birthday": "React warmly to the celebration already suggested by the scene.",
    "coffee": "Enjoy a quiet break in the existing morning or cafe setting.",
    "family": "Take a warm, relaxed moment in the existing everyday setting.",
    "romance": "React with a small natural smile to the existing surroundings.",
    "retro": "Enjoy a brief nostalgic everyday moment.",
    "humor": "Notice something amusing in the existing setting and react naturally.",
}


def lite_story_spec(base: dict) -> dict:
    """Keep CAM's source IDs and theme, not its old geometric/timing rules."""
    context = THEME_CONTEXT.get(base["storyTheme"], "a relaxed everyday moment")
    intent = THEME_INTENT.get(base["storyTheme"], "Take a short break in the existing scene.")
    plans = {}
    for ratio, old in base["aspectPlans"].items():
        opening = "The adult starts naturally in the existing scene."
        action = (
            "If standing, take one small natural step or shift stance once; "
            "if seated, gently settle into a comfortable position."
        )
        ending = "Finish with a relaxed expression and a natural settled posture."
        prompt = (
            f"Real-life {ratio} UGC, {context}. Animate the supplied on-body "
            "product reference. One simple continuous event: "
            f"{opening} {intent} {action} {ending} "
            "Keep the same adult, face, hair, body proportions, clothing and "
            "environment throughout this clip. For a new reference prefer natural "
            "adult proportions and casual jeans or khakis; never reshape a person. "
            "Natural unforced expression, "
            "steady observational camera, ordinary real-time movement. "
            "Keep the reference shirt and entire approved print visible in the frame; "
            "use a little framing clearance, no progressive push-in, and keep hands "
            "and existing props clear of the print. Do not add text or logos. "
            "No new props, people, wardrobe changes, extreme turns, loops or "
            "frozen-frame padding. Face naturally visible, no timed face quota."
        )
        plans[ratio] = {
            "aspectRatio": ratio,
            "heroRequestId": old["heroRequestId"],
            "heroSeed": old["heroSeed"],
            "storyId": old["storyId"],
            "storyTitle": context,
            "meaningfulAction": action,
            "cameraDirection": "steady observational medium framing",
            "beats": [opening, action, ending],
            "prompt": prompt,
        }
    return {
        "storyVersion": base["storyVersion"],
        "storyOutputContract": LITE_OUTPUT,
        "storySourceContract": LITE_SOURCE,
        "creativePolicy": LITE_POLICY,
        "storyId": base["storyId"],
        "storyTheme": base["storyTheme"],
        "targetDurationSeconds": LITE_TARGET_SECONDS,
        "storyStructure": "simple_beginning_action_resolution",
        "characterPolicy": "same_adult_identity_appearance_and_wardrobe_within_each_clip",
        "narrativePrompt": plans["9:16"]["prompt"],
        "aspectPlans": plans,
        "renderPolicy": {
            "preferredNativeTakesPerAspect": 1,
            "preferredDurationSeconds": LITE_TARGET_SECONDS,
            "acceptedNativeDurationSeconds": list(LITE_DURATION_BOUNDS),
            "durationIsFlexible": True,
            "mandatoryCut": False,
            "physicalPlacementProofRequired": False,
            "heroUploadRequired": False,
            "manualVideoApprovalRequiredForLinkage": False,
        },
    }


def worker_policy() -> dict:
    return {
        "version": LITE_POLICY,
        "outputContract": LITE_OUTPUT,
        "sourceContract": LITE_SOURCE,
        "preferredNativeTakesPerAspect": 1,
        "preferredDurationSeconds": LITE_TARGET_SECONDS,
        "acceptedNativeDurationSeconds": list(LITE_DURATION_BOUNDS),
        "creativeRequirements": [
            "one coherent everyday event with a natural beginning, action and resolution",
            "same adult character, appearance and wardrobe throughout each clip",
        ],
        "technicalGuards": [
            "approved artwork and C1717 product identity",
            "native MP4 in the requested aspect ratio, unchanged provider bytes",
            "valid lease and verified Drive destination; never overwrite saved media",
        ],
        "placementGeometryRequired": False,
        "twoTakesRequired": False,
        "heroUploadRequired": False,
        "independentQaRequiredForCamLinkage": False,
        "contentQaAfterUpload": "pending_review_not_pass",
        "instructions": (
            "For a command whose videoJobClaim.creativePolicy matches this policy, "
            "POST its exact claim body. Use the existing approved worn image when "
            "visually suitable, otherwise generate one simple native-aspect source. "
            "Use the returned short aspect prompt. Generate one native clip per "
            "missing ratio, preferably 6 seconds, and upload the actual native MP4 unchanged "
            "immediately. Do not add an external duration or codec gate. No mandatory hero upload, calibrated geometry, placement "
            "preflight, two takes, exact cut or timed face evidence. Do not fake "
            "content QA or stretch footage. Only HOLD visibly wrong product/design, "
            "unusable motion, wrong ratio, corrupt file, permission or provider error. "
            "Follow renderer retry policy and continue eligible work."
        ),
    }