"""Throughput-first V3.3: one native clip, a simple event, stable character.

No renderer is called here. Visual content facts are not certified by MP4 storage.
"""
from __future__ import annotations

LITE_POLICY = "CAM_STORY_CHARACTER_LITE_V1"
LITE_OUTPUT = "CAM_STORY_NATIVE_FLEX_V1"
LITE_SOURCE = "CAM_STORY_CHARACTER_SOURCE_V1"
LITE_TARGET_SECONDS = 6
LITE_DURATION_BOUNDS = (5.0, 12.5)

# Video hero preparation only. A visual size target, not a millimeter proof,
# a change to the Native Master, or an additional server-side upload gate.
PRINT_SCALE_POLICY = "CAM_VIDEO_COMPACT_PRINT_V1"

def hero_print_scale_policy() -> dict:
    return {
        "version": PRINT_SCALE_POLICY,
        "scope": "new_or_unsuitable_hero_for_authorized_missing_story_lite_aspect",
        "targetWidthPercentOfShirtFront": [35, 40],
        "maxHeightPercentOfCollarToHem": 40,
        "oversizedReferenceLinearScalePercent": 80,
        "firstInkBelowLowerCollarTargetMm": 150,
        "preserveArtworkAspectRatio": True,
        "physicalCalibrationVerified": False,
        "preserveSavedVideos": True,
        "preserveBankedValidNativeMp4": True,
        "instructions": (
            "For a newly needed hero only, use a compact natural front-center print. "
            "Aim for ink width 35-40% of the shirt body width at chest level, "
            "excluding sleeves; cap ink height at about 40% of collar-to-hem length. "
            "If a reference is oversized, reduce its ink dimensions to approximately "
            "80% once, or further as needed to fit the target. Never repeatedly shrink. "
            "Do not enlarge an intentionally smaller print or exceed an approved "
            "smaller print box. Preserve the exact Native Master ink, aspect ratio, "
            "layout, distress and colors. Top ink approximately 150 mm below the "
            "lower collar rib. Leave generous side and hem clearance. Maintain "
            "readable contrast and realistic fabric integration; no flat overlay. "
            "Use visual estimates, not claimed calibrated measurements. Reuse an "
            "existing suitable hero or valid banked native take. Never re-render "
            "a saved video or interrupt an active lease to apply this policy."
        ),
    }


def apply_hero_print_scale(story: dict, ratios) -> dict:
    """Append the compact visual instruction to selected missing hero plans."""
    policy = hero_print_scale_policy()
    for ratio in ratios:
        plan = story["aspectPlans"].get(ratio)
        if not isinstance(plan, dict) or "referenceImagePrompt" not in plan:
            continue
        plan["heroPrintScalePolicy"] = policy
        plan["referenceImagePrompt"] += " " + policy["instructions"]
    return story


# Keep the size policy stable for already pinned leases. This independent,
# versioned source-preparation instruction is adopted only on a new Lite lease.
HERO_REALISM_POLICY = "CAM_VIDEO_HERO_REALISM_V1"


def hero_realism_policy() -> dict:
    return {
        "version": HERO_REALISM_POLICY,
        "scope": "authorized_missing_story_lite_aspect_on_new_lease",
        "heroEditScope": "ink_only_if_reference_suitable_otherwise_rebuild_coherent_photo",
        "preservePersonAndCameraDuringPrintResize": True,
        "preservePrintScalePolicy": PRINT_SCALE_POLICY,
        "framing": "full_head_to_mid_thigh_or_knees_with_headroom_and_trousers",
        "camera": "natural_eye_to_chest_level_normal_lens_perspective",
        "environment": "credible_everyday_scene_with_consistent_scale_depth_light_and_shadows",
        "motion": "animate_person_only_lock_camera_background_and_anatomy",
        "sourceCheck": [
            "plausible adult head neck shoulders torso and limb proportions",
            "person and environment share coherent perspective lighting and scale",
            "full print shirt hem and meaningful trouser area visible without face enlargement",
            "current exact artwork and compact ink box remain readable",
        ],
        "rejectMaterialDefects": [
            "oversized head or face pasted onto mismatched body",
            "compressed torso or distorted shoulders neck or limbs",
            "implausible scene scale perspective or lighting with cutout appearance",
            "camera zoom crop drift anatomy or background changes during motion",
        ],
        "preserveSavedVideos": True,
        "additionalServerQaGate": False,
        "heroInstructions": (
            "Prioritize one coherent candid adult photograph over a close-up portrait. "
            "For a suitable reference, reduce ONLY the ink box once as required by the "
            "compact print policy; keep the same head, neck, shoulders, torso, limbs, "
            "garment fit, camera distance, framing, perspective and background. Never "
            "shrink the body or move the camera to make the print look smaller. If the "
            "reference already has distorted anatomy, cutout lighting or implausible "
            "perspective, prepare a coherent new on-body photo, not a pasted face or "
            "head replacement. A face reference guides appearance, never head size. "
            "Use a natural eye-to-chest-level viewpoint, normal-lens perspective "
            "(approximately 50 mm equivalent), enough camera distance and headroom. "
            "Frame the full head through mid-thighs or knees with visible trousers "
            "and full shirt hem; the head must not dominate the frame. Maintain natural "
            "individual adult proportions, believable neck and shoulders, real fabric "
            "drape and realistic human-scale surroundings. Use a simple authentic "
            "everyday setting with consistent depth, contact shadows and lighting on "
            "person and scene; avoid staged symmetric streets, impossible perspective "
            "and artificial cutout/composite appearance. Preserve exact artwork, "
            "contrast and the 150 mm top-ink target. Check the reference once before "
            "rendering; do not animate an unsuitable source."
        ),
        "motionInstructions": (
            "Animate only the person in the exact approved hero. Keep head-to-body "
            "proportions, garment fit, print size, camera distance, perspective, "
            "framing, background and lighting unchanged. No zoom, body compression, "
            "face enlargement, morphing or newly invented scene details."
        ),
        "qaInstructions": (
            "Use the existing lightweight source and sampled-frame visual check. "
            "Reject material anatomical distortion, implausible spatial scale or "
            "lighting, and camera/background drift, even if the print is readable. "
            "Natural body variation, expressions and blinking are not failures. "
            "Reuse a banked native take only if visually suitable; one correction "
            "within the existing budget, otherwise defer using the existing protocol. "
            "No new geometry proof, measurement quota, renderer call or upload gate."
        ),
    }


def apply_hero_realism(story: dict, ratios) -> dict:
    policy = hero_realism_policy()
    for ratio in ratios:
        plan = story["aspectPlans"].get(ratio)
        if not isinstance(plan, dict) or "referenceImagePrompt" not in plan:
            continue
        if plan.get("heroRealismPolicy", {}).get("version") == HERO_REALISM_POLICY:
            continue
        plan["heroRealismPolicy"] = policy
        # Resolve the original upper-thigh framing suggestion in the new scoped
        # reference only. The older base prompt remains byte-stable.
        plan["referenceImagePrompt"] = plan["referenceImagePrompt"].replace(
            "Frame head through upper thighs", "Frame full head through mid-thighs or knees", 1
        ) + " " + policy["heroInstructions"]
        plan["prompt"] += " " + policy["motionInstructions"]
    story["narrativePrompt"] = story["aspectPlans"]["9:16"]["prompt"]
    return story


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


# Reference preparation is separate from the video-generation prompt.
# Only the controlled theme enum enters either prompt; never copy title/SKU
# text onto the shirt. These are suggestions, not an additional QA contract.
THEME_MOTION = {
    "holiday": "gently glance at an existing festive detail",
    "outdoors": "make a small relaxed shift of posture",
    "ride": "settle into a comfortable stance",
    "gaming": "briefly glance aside as if taking a break",
    "sports": "relax the shoulders naturally",
    "birthday": "react with a natural cheerful expression",
    "pets": "glance briefly toward the existing surroundings",
    "coffee": "settle into a relaxed posture",
    "family": "share a warm natural smile",
    "romance": "smile naturally",
    "retro": "make a small comfortable posture shift",
    "humor": "react with a natural amused expression",
}


def reference_image_prompt(ratio: str, context: str) -> str:
    return (
        f"Photorealistic {ratio} lifestyle still, {context}. One adult in a "
        "washed short-sleeve crew-neck tee matching the approved garment and "
        "exact supplied Native Master print, with casual jeans or khakis. "
        "Frame head through upper thighs with comfortable clearance around "
        "the entire print, arms relaxed and clear of the chest. Natural "
        "skin texture, believable proportions, soft real-world lighting. "
        "Use the setting already appropriate to the theme. No objects in "
        "front of the print; no added text, garment codes, logos or design changes."
    )


def reference_motion_plan(ratio: str, context: str, theme: str) -> dict:
    action = THEME_MOTION.get(theme, "make a small relaxed posture shift")
    return {
        "referenceImagePrompt": reference_image_prompt(ratio, context),
        "meaningfulAction": action,
        "cameraDirection": "fixed medium framing matching the supplied hero",
        "beats": ["Begin in the supplied pose.", action.capitalize()+".",
                  "Settle naturally."],
        "prompt": (
            f"Animate the supplied hero in {ratio}. The adult should {action}, "
            "then settle naturally. Preserve the same person, shirt, exact print, "
            "trousers and scene. Fixed camera and framing; keep the full print "
            "unobstructed. Natural motion, blinking, gaze and smiles. No new text."
        ),
    }


def lite_story_spec(base: dict, *, reference_first: bool = True) -> dict:
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
        if reference_first:
            plans[ratio].update(reference_motion_plan(ratio, context, base["storyTheme"]))
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
        **({"promptStrategy": "reference_first"} if reference_first else {}),
        "aspectPlans": plans,
        "renderPolicy": {
            "preferredNativeTakesPerAspect": 1,
            "preferredDurationSeconds": LITE_TARGET_SECONDS,
            "acceptedNativeDurationSeconds": list(LITE_DURATION_BOUNDS),
            "durationIsFlexible": True,
            **({"referenceImagePromptField": "aspectPlans[].referenceImagePrompt",
                "referenceSelectionAdvisory": True} if reference_first else {}),
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
        "referenceImagePromptField": "aspectPlans[].referenceImagePrompt",
        "referenceSelectionAdvisory": True,
        "heroPrintScalePolicy": hero_print_scale_policy(),
        "heroRealismPolicy": hero_realism_policy(),
        "instructions": (
            "For a command whose videoJobClaim.creativePolicy matches this policy, "
            "POST its exact claim body. For new Lite work, reuse a visually suitable "
            "approved hero bound to the current artwork SHA and requested aspect. "
            "Check its print, garment, adult character, trousers and framing once "
            "before video generation. Follow the compact print-size guidance when "
            "the live aspect plan carries heroPrintScalePolicy. When the aspect also "
            "carries heroRealismPolicy, follow its ink-only edit, coherent anatomy, "
            "framing and environment guidance, including the existing visual check. "
            "Do not apply a newer "
            "policy to an already active lease lacking that marker. If unsuitable, "
            "use the separate referenceImagePrompt "
            "to prepare a corrected source, or defer if unavailable. This is source "
            "preparation, not a new CAM proof or QA gate. Send only the aspect motion "
            "prompt and exact hero to the renderer; do not append listing titles, "
            "SKU text, automation instructions or old QA gates. Prefer one native "
            "take and at most one material-error correction within the existing budget. "
            "Natural blinking, smiles and gaze are not defects. Use a short final "
            "product, print and severe character-error check; upload the native MP4 "
            "unchanged immediately. Six seconds is a preference, not a new duration "
            "or codec gate. No mandatory hero upload, geometry, two takes or timed "
            "face evidence. Never fake QA or downgrade a pinned strict contract. "
            "Respect all live holds, provider limits and retry rules."
        ),
    }