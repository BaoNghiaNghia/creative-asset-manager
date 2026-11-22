"""CAM Muse 12-second story-led UGC prompts for future, unrendered variants.

Pure, dependency-free, deterministic planning. Listing titles are treated as
untrusted *theme labels*, never copied into the renderer prompt or used as art.
The approved CAM SKU (not title keywords) determines garment construction.
"""
from __future__ import annotations

import hashlib
import re

STORY_VERSION = "CAM_STORY_REAL_LIFE_V3_3"
STORY_DURATION_SECONDS = 12
STORY_MAX_NATIVE_TAKE_SECONDS = 10
STORY_OUTPUT_CONTRACT = "CAM_STORY_12S_TWO_TAKE_V1"
STORY_SOURCE_CONTRACT = "CAM_STORY_DUAL_HERO_FACE_V1"
FACE_VISIBLE_MIN_SECONDS = 8.0
STORY_FINAL_DURATION_BOUNDS = (11.5, 12.5)
STORY_WARDROBE_POLICY = "CAM_STORY_WARDROBE_CASTING_V1"

# Applies to new V3.3 story renders only; choose a suitable approved model
# and source frame before generating, NEVER re-shape a real person's body or
# replace trousers after the fact.
WARDROBE_CASTING = {
    "allowedBottoms": [
        "classic denim jeans (blue, black or neutral washed denim)",
        "casual khaki/chino trousers (cotton twill, beige, tan or olive)",
    ],
    "bottomFit": "straight or relaxed natural trouser fit; believable waist, hips and leg drape",
    "subjectBuild": (
        "adult model with an average/medium or moderately athletic build, "
        "balanced, realistic proportions; avoid exaggerated or very bulky silhouettes"
    ),
    "preRenderSelection": (
        "Select an approved starting frame already showing the required "
        "jeans or khaki trousers and intended proportionate model build. "
        "If not available, choose a different approved frame or BLOCK."
    ),
    "noAlteration": (
        "Never slim, reshape, retouch or otherwise change an existing person's "
        "body; never transform shorts, joggers or other clothing into trousers."
    ),
    "visibility": (
        "Show enough waist/upper-thigh area to recognize the trousers for at "
        "least two continuous seconds per aspect ratio, while keeping the "
        "entire front shirt print readable."
    ),
    "reject": [
        "shorts, leggings, sweatpants, joggers, skirts or dress trousers instead of jeans or khaki chinos",
        "unrealistic, exaggerated or inconsistent torso/waist/hip/leg proportions",
        "body slimming, reshaping, wardrobe morphing, or changing pants between frames",
        "pants hidden for the whole video so garment type cannot be verified",
        "inconsistent pants or model body between take A and take B of the SAME video",
    ],
}

# Authoritative SKU must come from CAM job metadata / an approved garment
# reference. This catalog never infers the garment from marketplace titles.
GARMENT_PROFILES = {
    "C1717": {
        "name": "Comfort Colors C1717",
        "form": "garment-dyed heavyweight cotton short-sleeve crew-neck T-shirt; relaxed, boxy unisex drape",
        "reject": ["long sleeves", "crewneck sweatshirt", "hoodie", "zip hoodie",
                   "slim athletic fit", "oversized artificial neckline"],
    },
    "G500": {
        "name": "Gildan G500",
        "form": "classic cotton short-sleeve crew-neck T-shirt; approved regular unisex fit",
        "reject": ["long sleeves", "sweatshirt", "hoodie", "slim athletic fit"],
    },
    "G180": {
        "name": "Gildan G180",
        "form": "crewneck sweatshirt with long sleeves, ribbed cuffs and hem; no hood",
        "reject": ["T-shirt", "hood", "front kangaroo pocket", "zipper"],
    },
    "G185": {
        "name": "Gildan G185",
        "form": "pullover hoodie with hood, drawstrings, long sleeves and front pouch",
        "reject": ["short sleeves", "crewneck without hood", "zip-front jacket"],
    },
}

# These themes are determined from bounded keywords only. Source title text
# must never be repeated in a generative prompt (prompt-injection protection).
THEME_KEYWORDS = (
    ("outdoors", ("hiking", "hike", "hiker", "adventure", "mountain", "trail", "camp",
                  "camping", "yosemite", "national park", "outdoor", "backpacking")),
    ("ride", ("ride", "rider", "motorcycle", "biker", "road trip", "motorbike", "bike life")),
    ("holiday", ("christmas", "xmas", "santa", "holiday", "halloween", "easter", "festive")),
    ("gaming", ("gaming", "gamer", "arcade", "pixel", "game", "controller")),
    ("sports", ("hockey", "baseball", "basketball", "football", "soccer", "golf",
                "tennis", "pickleball", "volleyball")),
    ("birthday", ("birthday", "born", "anniversary", "40th", "50th", "30th")),
    ("pets", ("dog", "puppy", "cat", "kitten", "pet", "paws", "horse", "bee", "tiger")),
    ("coffee", ("coffee", "caffeine", "espresso", "latte", "barista", "bakery", "bread")),
    ("family", ("mama", "mom", "mother", "dad", "papa", "grandma", "grandpa",
                "teacher", "school", "family")),
    ("romance", ("valentine", "love", "heart", "couple", "wedding", "wife", "husband")),
    ("retro", ("vintage", "retro", "nostalgia", "90s", "80s", "classic", "old school")),
    ("humor", ("funny", "sarcastic", "joke", "humor", "meme", "tired", "introvert")),
)

# A story has a beginning, a REAL action, and a natural end in one location.
# Any referenced object must already exist in the approved starting scene.
# No extra actors, objects, vehicles or wardrobe may be hallucinated.
STORIES = {
    "outdoors": (
        ("trail_arrival", "A pause on a walk", "a walkable park path with safe open space",
         "Already walking along the visible path at an ordinary human pace, taking two forward steps.",
         "Walk another two or three steps around a real bend in the path, slow down naturally and shift position to face the view; fabric and arms follow body movement.",
         "Stop at a real vantage point, breathe, appreciate the view and continue one small step forward, not backwards."),
        ("scenic_detour", "A small detour", "a real path or accessible outdoor walkway",
         "Notice a turn in the existing walkway while already in motion, and begin following it.",
         "Change walking direction once with a real foot pivot, take three natural steps and adjust pace as the setting opens.",
         "Arrive, stop balanced on both feet, relax the shoulders and look briefly toward the real surroundings."),
    ),
    "ride": (
        ("heading_out", "Heading out for an evening", "an existing doorway, gate or driveway with a walkable exit",
         "Step through or toward the visible doorway with a clear destination, not a static pose.",
         "Continue out through the opening and take several normal steps toward the visible exit, briefly adjusting the shirt hem at the side without touching artwork.",
         "Pause in the real open space to orient yourself, then continue forward with purpose; do not invent a motorcycle."),
        ("evening_walk", "On the way somewhere", "an approved outdoor sidewalk or accessible path",
         "Enter the shot already walking forward, one hand swinging naturally by the side.",
         "Follow the actual path for three or four purposeful steps, notice the real street surroundings, and slow near an existing corner.",
         "Make one believable change of direction and move on, rather than pacing left and right."),
    ),
    "holiday": (
        ("party_arrival", "Arriving for a gathering", "an approved cozy indoor room with a visible doorway",
         "Walk into the decorated or ordinary room from the existing doorway, with relaxed anticipation.",
         "Cross a few steps into the room and move toward an already visible place to stand, naturally acknowledging the atmosphere.",
         "Settle into the room with an easy smile and a small step onward; no fabricated gifts, guests or decorations."),
        ("holiday_exit", "Off to celebrate", "an existing foyer or clear room-to-door walking route",
         "Take a last look at the room while beginning to leave, with feet and hips already moving.",
         "Cross the floor and gently move through the real doorway, turning the torso naturally while keeping the full print unobstructed.",
         "Step into the next visible space, pause comfortably and continue toward the real destination."),
    ),
    "gaming": (
        ("desk_break", "A break between rounds", "a real chair and desk in the approved source scene",
         "Push back from the existing desk and rise naturally from the chair, hands staying clear of the chest artwork.",
         "Stand fully, stretch shoulders in a small authentic recovery movement and walk two steps away from the desk.",
         "Return to a comfortable standing position with a genuine relieved smile; no invented controller or monitor."),
        ("after_game", "Getting up for a break", "a real indoor chair with room to walk",
         "Lean forward with intention, feet planting on the floor to stand up from the existing chair.",
         "Stand in one continuous movement, walk across the available floor, and naturally turn toward an existing exit.",
         "Stop just before the visible exit, adjust posture once, and continue in that direction."),
    ),
    "sports": (
        ("after_practice", "The walk after practice", "a visible walkway, court-side corridor or gym exit",
         "Walk at a natural, slightly tired pace along the actual accessible route, arms loose.",
         "Continue for three purposeful steps, slow down and take a natural deep breath as if ending an active day.",
         "Straighten up and move toward the real exit with a small satisfied smile, no fake workout motions."),
        ("arrival", "Arriving to join in", "a real courtyard, sidewalk or venue entrance",
         "Walk toward the visible venue or doorway with purposeful forward movement.",
         "Turn at a real corner, keep walking, and naturally check the environment while hands stay below print.",
         "Pause near the actual entrance, then continue inside the existing route."),
    ),
    "birthday": (
        ("celebration_arrival", "Ready to celebrate", "a real room or hallway with enough space to walk",
         "Enter the room with the energy of someone arriving for an occasion.",
         "Walk toward a real doorway or gathering area already visible, turning the body in one smooth natural path.",
         "Finish with a genuine modest smile and move forward; do not invent balloons, cakes or guests."),
        ("getting_ready", "On the way out", "a real home entryway or interior path",
         "Walk across the visible room, already in the middle of getting ready to leave.",
         "Take a few realistic steps toward the existing doorway and adjust the shirt hem lightly at the side, never over the print.",
         "Leave the space with an upbeat natural expression and a final forward step."),
    ),
    "pets": (
        ("park_day", "A walk in the park", "an approved park path or garden walkway",
         "Begin walking at a relaxed but real pace along the visible path.",
         "Take three or four forward steps, notice something in the actual surroundings and follow the route naturally.",
         "Slow near the real path edge, smile briefly, and continue; do not generate any animal not already visible."),
        ("courtyard", "A small moment outside", "a safe courtyard or accessible garden path",
         "Walk out from the existing threshold and move toward the open courtyard.",
         "Take several natural steps across the safe path and turn once around a real feature in the scene.",
         "Settle into the outdoors and continue the activity, without inventing a pet or a leash."),
    ),
    "coffee": (
        ("cafe_arrival", "A stop on the way", "an actual café or storefront with a visible entrance",
         "Walk toward the real storefront entrance at ordinary walking speed.",
         "Reach the existing door, open it gently if accessible and take two steps inside without covering the shirt artwork.",
         "Pause inside and naturally survey the existing setting; no new drink or cup appears."),
        ("window_break", "A short break", "a real café/window or seated indoor space with clear walking room",
         "Stand from the existing chair or begin stepping toward a real window.",
         "Cross the available floor in two or three steps and approach the window in one continuous action.",
         "Stop to take in the actual view, breathe once and turn back toward the room."),
    ),
    "family": (
        ("weekend_exit", "A weekend errand", "a real doorway and walkable room",
         "Start moving toward the existing exit as someone beginning an ordinary day.",
         "Cross the room naturally, open or pass the real doorway, and take a few steps outside or into the next room.",
         "Pause with calm warmth, then continue on the real route with the shirt front visible."),
        ("walk_together", "A relaxed weekend", "a real neighborhood sidewalk or park walkway",
         "Walk forward along the clear path with easy, purposeful steps.",
         "Turn around an actual bend or obstacle while maintaining natural foot placement and relaxed arms.",
         "Settle into a comfortable stride, subtly acknowledging the setting; do not generate extra people."),
    ),
    "romance": (
        ("going_to_meet", "On the way to meet someone", "a real sunny sidewalk or indoor corridor",
         "Begin moving with quiet anticipation along the existing path.",
         "Walk three to four steps, follow a real corner or doorway and slow down naturally.",
         "Take one final step forward with an unforced smile; do not invent a partner or romantic prop."),
        ("little_outing", "A casual outing", "a real garden path, courtyard or cafe entrance",
         "Walk into the visible space as if heading to a relaxed outing.",
         "Take several purposeful steps, change direction around a genuine environmental feature, and stop in balance.",
         "Glance briefly at the scene with a genuine smile, then continue forward."),
    ),
    "retro": (
        ("city_walk", "A casual city walk", "an actual sidewalk or town walkway",
         "Already be walking with a relaxed stride, hands naturally at the sides.",
         "Walk four real forward steps, slow near an existing storefront or crossing, and follow the safe route.",
         "Continue past the real feature, maintaining a natural pace and visible shirt front."),
        ("neighborhood_stroll", "A familiar neighborhood", "a real outdoor courtyard or street path",
         "Enter the shot in the middle of a normal walk rather than standing at attention.",
         "Step around a visible feature, change direction once with hips and legs moving together, then walk onward.",
         "Stop briefly to take in the surroundings and keep going, no repeated pendulum motion."),
    ),
    "humor": (
        ("small_surprise", "An ordinary little surprise", "a real room or clear walkway",
         "Walk into the existing space and notice something genuinely present in the scene.",
         "Take a few steps, briefly react with a spontaneous smile, and continue the task instead of staring at the lens.",
         "Finish the action with a real forward step, expression settling naturally."),
        ("running_late", "A slightly hurried moment", "a real indoor route with a visible doorway",
         "Start moving through the room with small, purposeful urgency, not frantic or theatrical.",
         "Walk across the available floor, turn naturally through the existing door or hallway and ease the pace.",
         "Arrive where the path leads, breathe and offer a brief amused look at the real situation."),
    ),
    "everyday": (
        ("step_outside", "Stepping into the day", "a real room-to-door path or safe porch",
         "Walk toward the existing door with a relaxed, normal pace.",
         "Go through the doorway and take three steps along the real path without changing the shirt or setting.",
         "Pause naturally in the new visible space and continue one small step onward."),
        ("on_the_move", "A short walk", "a real accessible path with room to move",
         "Begin already walking on the visible path rather than posing still.",
         "Take three or four genuinely forward steps, follow a real turn, and shift pace naturally.",
         "Stop briefly, absorb the surrounding scene and continue forward."),
    ),
}


CAPTURE_STYLES = (
    "The phone observes from a stable front-three-quarter angle while the PERSON "
    "actually crosses the space. Do not let camera drift substitute for motion.",
    "A handheld observer takes one or two safe parallel steps following the "
    "PERSON's forward route, then settles. Keep the exact shirt front readable.",
    "Use a calm, nearly stationary phone position as the PERSON naturally "
    "enters, passes through and settles in the existing space; the actor "
    "supplies the visual movement, not a pan or zoom.",
)


def detect_theme(title: str) -> str:
    """Classify bounded theme cues only; raw titles never enter render prompts."""
    words = set(re.findall(r"[a-z0-9]+", title.lower()[:700]))
    for theme, cues in THEME_KEYWORDS:
        if any(set(cue.split()).issubset(words) for cue in cues):
            return theme
    return "everyday"


def build_story_spec(
    *,
    title: str,
    listing_key: str,
    artwork_sha256: str,
    garment_code: str = "C1717",
) -> dict:
    """Deterministic independent 9:16 and 16:9 story and hero plans.

    garment_code MUST be a trusted CAM SKU, never inferred from title.
    """
    if garment_code not in GARMENT_PROFILES:
        raise ValueError("Unknown or unapproved garment SKU")
    if not re.fullmatch(r"[0-9]{5,20}", listing_key):
        raise ValueError("Invalid CAM listing identity")
    if not re.fullmatch(r"[a-f0-9]{64}", artwork_sha256):
        raise ValueError("Approved artwork SHA-256 required")
    theme = detect_theme(title)
    choices = STORIES[theme]
    seed = hashlib.sha256(
        f"{listing_key}:{artwork_sha256}:{theme}:{STORY_VERSION}".encode()
    ).digest()
    selection = seed[0] % len(choices)
    camera = CAPTURE_STYLES[seed[1] % len(CAPTURE_STYLES)]
    story_id, title_safe, scene, hook, action, ending = choices[selection]
    garment = GARMENT_PROFILES[garment_code]
    beats = [
        {"startSeconds": 0, "endSeconds": 3, "purpose": "context", "action": hook},
        {"startSeconds": 3, "endSeconds": 9, "purpose": "meaningful_action", "action": action},
        {"startSeconds": 9, "endSeconds": 12, "purpose": "natural_payoff", "action": ending},
    ]
    garment_name = garment["name"]
    prompt = (
        f"ORIGINAL 12-second real-life UGC mini-story, '{title_safe}'. "
        "ONE coherent believable human event in ONE approved location. "
        "Capture two authentic moving shots, six seconds of usable motion in "
        "each, with exactly ONE motivated cut at 6 seconds. Each native "
        "source take can be at most 10 seconds: trim real frames to make "
        "two six-second shots, then assemble a 12-second MP4. If a genuine "
        "single native 12-second take is actually available, it is also "
        "acceptable, but never assume the current Muse renderer supports it. "
        "Keep the same person, jeans/khakis, approved T-shirt, print, light "
        "and environment across the cut. No frozen talking-head poses, "
        "camera-only motion, repeated frames, reversed footage, slow-motion "
        "padding, synthetic hold or filler. No added screen text, music, "
        "voiceover or captions. "
        f"The approved start frame MUST show a genuine {garment_name}: {garment['form']}. "
        "Use exactly the approved shirt COLOR, fit, sleeve length and collar; "
        "DO NOT turn a T-shirt into a sweatshirt or hoodie. "
        "Wardrobe and casting are mandatory: the adult model has a natural "
        "average/medium or moderately athletic, proportionate silhouette, "
        "not an exaggerated bulky or very heavyset look. The model MUST "
        "wear classic denim JEANS OR casual cotton-twill KHAKI/CHINO TROUSERS "
        "with a natural straight or relaxed fit, never shorts, leggings, "
        "sweatpants, joggers, or a skirt. Match the garment, model and "
        "pants already present in an approved starting frame; choose another "
        "approved frame or BLOCK if the requested trousers and body build "
        "are missing. NEVER reshape or digitally slim an existing person, "
        "swap trousers during animation or invent clothing. Show the "
        "trouser waistband and upper thighs clearly for at least two "
        "continuous seconds in BOTH aspect ratios while keeping the full "
        "front artwork visible. "
        "For EACH ratio use ONLY its own aspectPlans[ratio].prompt, "
        "aspectPlans[ratio].sceneRequiresApprovedStartFrame, storyId, "
        "cameraDirection, heroRequestId and heroSeed. Do not reuse a "
        "shared setup, prompt, scene, frame or take across ratios. "
        "The starting scene must match the corresponding aspect plan and "
        "ALREADY contain any story object; if not, choose another independent "
        "source image or BLOCK. 0-3s (hook, shot 1) establishes the aspect "
        "story; 3-9s (observable action) has real purposeful human movement; "
        "9-12s (resolution, shot 2) completes that aspect-specific event. "
        "Make leg, hip, torso, shoulder, hand and cotton-fabric motion physically "
        "coherent and driven by the corresponding action. "
        "Camera handheld but OBSERVATIONAL, "
        "not moving in place of the subject; no head-turn-only or sway-only video. "
        "All story objects and other people must already be present in the "
        "approved reference frame: do NOT generate new cars, pets, gifts, drinks, "
        "furniture, signs, doors or companions. Never add extra artwork or text. "
        "Preserve the exact approved Native Master print pixels/colors/wording, "
        "scale and placement, entirely visible on the shirt across each frame. "
        "The FIRST flat-ink pixel is 150 mm below the lower collar rib measured "
        "along garment fabric, same print box as the approved product image; "
        "NO embroidery, drift, warping, stretching or occluding hands. "
        "The full adult FACE must remain naturally visible, recognizable and "
        "well lit for AT LEAST 8 of 12 seconds; never crop at the neck, "
        "mask the face, generate a headless torso, or use a chest-only video. "
        "Keep the face AND entire approved chest print simultaneously visible "
        "in the main moving shots, with waist/upper thighs shown for pants QA. "
        "Generate TWO INDEPENDENT ORIGINAL HERO IMAGES: one true portrait 9:16 "
        "and one true landscape 16:9. They MUST be generated separately using "
        "different prompts, seeds, generation request IDs, compositions and "
        "natural actions, never derived from a single source image, video, "
        "frame, resize, crop, outpaint or reframe. Use distinct aspectPlans "
        "for the two independent shoots; sharing the same approved Native Master "
        "artwork is required, but sharing the same source hero frame is FORBIDDEN. "
        "The 12-second FINAL export must be continuous in story, "
        "even when it uses two genuinely recorded takes. Do not invent "
        "additional story time with looped/reversed frames, duplicated "
        "stills, frame freezing or time stretching. If two usable matching "
        "takes or a verified native 12-second source are unavailable, "
        "report BLOCKED rather than outputting fake 12-second video."
    )
    # The two ratios are two independently generated images and shoots, not
    # alternate crops of one still. Per-ratio request IDs/seeds are stable for
    # retries but guaranteed distinct within the same listing.
    aspect_plans = {}
    for ratio, selection_offset, camera_offset, role, framing in (
        ("9:16", 0, 0, "vertical_original_hero",
         "portrait medium-full UGC frame showing natural face, complete shirt "
         "print and waist/jeans or khakis; face visible through the main action"),
        ("16:9", 1, 1, "horizontal_original_hero",
         "landscape environmental medium-wide UGC frame with full face, "
         "complete chest print and waist visible; native wide composition"),
    ):
        versioned = f"{listing_key}:{artwork_sha256}:{STORY_SOURCE_CONTRACT}:{ratio}"
        generated = hashlib.sha256(versioned.encode()).digest()
        variant = choices[(selection + selection_offset) % len(choices)]
        variant_id, variant_title, variant_scene, variant_hook, variant_action, variant_end = variant
        camera_variant = CAPTURE_STYLES[
            (seed[1] + camera_offset) % len(CAPTURE_STYLES)
        ]
        hero_request_id = generated.hex()[:32]
        # Same design base seed plus a nonzero ratio offset; cannot collide.
        hero_seed = (int.from_bytes(seed[4:8], "big") +
                     selection_offset * 0x9E3779B9) % (2 ** 32)
        aspect_prompt = (
            f"INDEPENDENT {ratio} ORIGINAL image-to-video shoot for the SAME "
            f"approved {garment_name} printed product. Generate a UNIQUE hero "
            f"source image from a FRESH image generation request ID "
            f"{hero_request_id} and separate seed {hero_seed}. "
            f"This is NOT a resized, cropped, reframed, outpainted or "
            f"re-encoded image of the other aspect. Exact native {ratio} "
            f"image framing: {framing}. "
            "Show a realistic adult's FULL FACE and expressive eyes clearly "
            "for at least 8 seconds of the final 12-second video; no face-free "
            "torso or neck-down crop, even as a fallback. "
            f"Location and visible source objects: {variant_scene}. "
            f"0-3s: {variant_hook} 3-9s: {variant_action} "
            f"9-12s: {variant_end} "
            f"Camera: {camera_variant} "
            "One truthful scene and TWO real 6-second moving takes joined by "
            "one deliberate cut at second 6, with the SAME person, real "
            "clothes and source scene within this ratio. Use natural "
            "human motion, not camera-only motion or a stretched still. "
            "Wear only denim jeans or khaki chinos, adult with natural "
            "medium/average or moderately athletic proportions; never "
            "alter a real person's body. Keep the T-shirt exactly the "
            "CAM-approved color, relaxed short-sleeve fit and Native Master "
            "flat ink, first ink exactly 150mm below the lower collar rib. "
            "Maintain face, print and natural cotton drape throughout. "
            "Never reuse any source image, generated take, frame, "
            "video bytes, seed or hero generation request from the other "
            "aspect. Only the approved product/artwork identity may be shared."
        )
        aspect_plans[ratio] = {
            "aspectRatio": ratio,
            "heroRole": role,
            "heroRequestId": hero_request_id,
            "heroSeed": hero_seed,
            "heroGeneration": "new_independent_image_request",
            "heroImageMustBeOriginalForRatio": True,
            "heroImageSource": "muse_generated_distinct_and_cam_drive_verified",
            "sceneRequiresApprovedStartFrame": variant_scene,
            "storyId": variant_id,
            "storyTitle": variant_title,
            "cameraDirection": camera_variant,
            "framing": framing,
            "faceVisibleMinSeconds": FACE_VISIBLE_MIN_SECONDS,
            "faceAndPrintVisibleTogether": True,
            "showWaistForBottomsQaSeconds": 2,
            "shotA": {"seconds": [0, 6], "action": variant_hook},
            "shotB": {"seconds": [6, 12], "action": variant_end},
            "meaningfulAction": variant_action,
            "prompt": aspect_prompt,
        }
    assert aspect_plans["9:16"]["heroRequestId"] != aspect_plans["16:9"]["heroRequestId"]
    assert aspect_plans["9:16"]["heroSeed"] != aspect_plans["16:9"]["heroSeed"]

    return {
        "storyVersion": STORY_VERSION,
        "storyOutputContract": STORY_OUTPUT_CONTRACT,
        "storySourceContract": STORY_SOURCE_CONTRACT,
        "storyId": story_id,
        "storyTheme": theme,
        "storyTitle": title_safe,
        "cameraDirection": camera,
        "targetDurationSeconds": STORY_DURATION_SECONDS,
        "storyStructure": "one_coherent_three_act_event_two_real_motion_shots",
        "sceneRequiresApprovedStartFrame": scene,
        "beats": beats,
        "wardrobeCastingLock": {
            "policyVersion": STORY_WARDROBE_POLICY,
            **WARDROBE_CASTING,
            "samePantsAndPersonAcrossRatios": False,
            "consistentWithinEachVideoAcrossItsTwoTakes": True,
            "onlyApprovedSourceFrame": True,
        },
        "garmentLock": {
            "sku": garment_code,
            "product": garment_name,
            "construction": garment["form"],
            "sourceOfTruth": "cam_approved_garment_not_listing_title",
            "matchApprovedBaseColorAndFit": True,
            "rejectGarmentTypes": garment["reject"],
        },
        "printLock": {
            "source": "approved_native_master_sha256",
            "artworkSha256": artwork_sha256,
            "firstInkBelowLowerCollarMm": 150,
            "method": "flat_ink_print_only",
            "rejectChangesAcrossFrames": True,
        },
        "narrativePrompt": prompt,
        "aspectPlans": aspect_plans,
        "sourceProofPolicy": {"version": STORY_SOURCE_CONTRACT,
            "heroImageUploadsPerListing": 2,
            "oneNewOriginalHeroPerAspect": True,
            "requireDifferentImageContent": True,
            "requireIndependentGenerationIdsAndSeeds": True,
            "rejectCrossAspectVideoConversion": True,
            "manualFaceAndVisualDifferenceQaPending": True},
        "qaPolicy": {
            "requireMeaningfulActionSeconds": [3, 9],
            "reject": [
                "only head swivels, blinks, eye glances, shoulder sway or camera pan",
                "model standing in one spot for almost all 12 seconds",
                "looped forward-backwards walking, repeated hand gesture or frozen ending",
                "unmotivated movement or action inconsistent with approved starting scene",
                "incorrect garment SKU, sleeves, neckline, fit, base color or fabric",
                *WARDROBE_CASTING["reject"],
                "props, companions or vehicles hallucinated into the source scene",
                "print size, lettering, color, SHA fidelity, 150 mm anchor or shape changed",
                "two aspect ratios reuse one hero photo or derive one video from the other",
                "face absent, cut off at the neck, hidden, or visible for fewer than 8 of 12 seconds",
                "vertical 9:16 source stretched, reframed, outpainted or cropped into horizontal 16:9",
                "different face, shirt, artwork, trousers or environment within the two takes of one video",
                "12 seconds assembled with duplicated/frozen/reversed frames, synthetic holds or slowed footage",
                "two shots fail to match the approved actor, trousers, shirt, print or location",
            ],
            "review": "independent garment, story-action, print and fabric motion QA per ratio",
            "storageIsNotApproval": True,
            "faceVisibleMinSeconds": FACE_VISIBLE_MIN_SECONDS,
            "independentHeroImageReviewRequired": True,
            "faceVisibilityReviewRequired": True,
        },
        "renderPolicy": {
            "outputContractVersion": STORY_OUTPUT_CONTRACT,
            "targetFinalDurationSeconds": STORY_DURATION_SECONDS,
            "maxNativeDurationSecondsPerTake": STORY_MAX_NATIVE_TAKE_SECONDS,
            "minAcceptableFinalDurationSeconds": STORY_FINAL_DURATION_BOUNDS[0],
            "maxAcceptableFinalDurationSeconds": STORY_FINAL_DURATION_BOUNDS[1],
            "preferredAssembly": "two_genuine_motion_shots_motivated_cut",
            "nativeTakes": [
                {
                    "shot": 1, "useFinalSeconds": [0, 6],
                    "sourceLimitSeconds": STORY_MAX_NATIVE_TAKE_SECONDS,
                    "action": "start situation, initiate purposeful motion",
                },
                {
                    "shot": 2, "useFinalSeconds": [6, 12],
                    "sourceLimitSeconds": STORY_MAX_NATIVE_TAKE_SECONDS,
                    "action": "continue purposeful motion and complete the story",
                },
            ],
            "allowedAlternative": "single_native_12s_only_if_real_capability_is_verified",
            "assemblyQa": "independent_genuine_motion_continuity_identity_and_print_check",
            "missingUsableTakes": "block_no_loop_or_speed_stretch",
            "startFrameMismatch": "choose_another_approved_source_or_block",
            "existingSavedVideo": "never_regenerate_or_replace",
            "aspectRatios": ["9:16", "16:9"],
            "sameStoryForBothRatios": False,
            "differentHeroImagePerAspect": True,
            "differentGenerationRequestAndSeedPerAspect": True,
            "noCrossAspectCropOrReuse": True,
            "sourceContract": STORY_SOURCE_CONTRACT,
        },
    }