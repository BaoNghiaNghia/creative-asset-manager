"""CAM Muse 14-second story-led UGC prompts for future, unrendered variants.

Pure, dependency-free, deterministic planning. Listing titles are treated as
untrusted *theme labels*, never copied into the renderer prompt or used as art.
The approved CAM SKU (not title keywords) determines garment construction.
"""
from __future__ import annotations

import hashlib
import re

STORY_VERSION = "CAM_STORY_REAL_LIFE_V3_3"
STORY_DURATION_SECONDS = 14

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
    """Deterministic story per design; same narrative for both aspect ratios.

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
        {"startSeconds": 3, "endSeconds": 10, "purpose": "meaningful_action", "action": action},
        {"startSeconds": 10, "endSeconds": 14, "purpose": "natural_payoff", "action": ending},
    ]
    garment_name = garment["name"]
    prompt = (
        f"ORIGINAL 14-second real-life UGC mini-story, '{title_safe}'. "
        "ONE continuous believable human event in ONE approved location, "
        "without jump cuts, frozen talking-head poses, camera-only movement, "
        "looping or slow-motion padding. No added screen text, music, voiceover or captions. "
        f"The approved start frame MUST show a genuine {garment_name}: {garment['form']}. "
        "Use exactly the approved shirt COLOR, fit, sleeve length and collar; "
        "DO NOT turn a T-shirt into a sweatshirt or hoodie. "
        f"The starting scene must ALREADY contain {scene}; if it does not, "
        "choose another approved start image before rendering, otherwise BLOCK. "
        f"0-3s (hook): {hook} "
        f"3-10s (observable action): {action} "
        f"10-14s (resolution): {ending} "
        "Make leg, hip, torso, shoulder, hand and cotton-fabric motion physically "
        f"coherent and driven by the action. Camera treatment: {camera} "
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
        "Create matching 9:16 and 16:9 views of the SAME event, with natural "
        "framing rather than cropping the graphic. "
        "If native 14-second output is unavailable, report BLOCKED: never "
        "manufacture duration with a reversed loop, frame freezing or stretching."
    )
    return {
        "storyVersion": STORY_VERSION,
        "storyId": story_id,
        "storyTheme": theme,
        "storyTitle": title_safe,
        "cameraDirection": camera,
        "targetDurationSeconds": STORY_DURATION_SECONDS,
        "storyStructure": "one_continuous_take_three_act_real_life",
        "sceneRequiresApprovedStartFrame": scene,
        "beats": beats,
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
        "qaPolicy": {
            "requireMeaningfulActionSeconds": [3, 10],
            "reject": [
                "only head swivels, blinks, eye glances, shoulder sway or camera pan",
                "model standing in one spot for almost all 14 seconds",
                "looped forward-backwards walking, repeated hand gesture or frozen ending",
                "unmotivated movement or action inconsistent with approved starting scene",
                "incorrect garment SKU, sleeves, neckline, fit, base color or fabric",
                "props, companions or vehicles hallucinated into the source scene",
                "print size, lettering, color, SHA fidelity, 150 mm anchor or shape changed",
                "two aspect ratios portray different events or shirt variants",
                "length built from repeated/frozen frames instead of native 14 seconds",
            ],
            "review": "independent garment, story-action, print and fabric motion QA per ratio",
            "storageIsNotApproval": True,
        },
        "renderPolicy": {
            "maxNativeDurationSeconds": STORY_DURATION_SECONDS,
            "minAcceptableNativeDurationSeconds": 13.5,
            "maxAcceptableNativeDurationSeconds": 14.5,
            "missingNativeDuration": "block_no_loop_or_speed_stretch",
            "startFrameMismatch": "choose_another_approved_source_or_block",
            "existingSavedVideo": "never_regenerate_or_replace",
            "aspectRatios": ["9:16", "16:9"],
            "sameStoryForBothRatios": True,
        },
    }
