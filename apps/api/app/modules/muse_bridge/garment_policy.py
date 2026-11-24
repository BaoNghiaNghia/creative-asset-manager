"""Fixed C1717 garment policy for Muse image/video prompts.

Marketplace title text is untrusted SEO metadata, never garment selection or
render instructions. Preserve it in the CAM database; expose only a bounded,
garment-neutral description to external generation workers.
"""
from __future__ import annotations

import re

GARMENT_POLICY_VERSION = "CAM_VIDEO_C1717_GARMENT_LOCK_V1"
GARMENT_SKU = "C1717"
GARMENT_NAME = "Comfort Colors 1717"
GARMENT_CONSTRUCTION = (
    "garment-dyed heavyweight cotton short-sleeve crew-neck T-shirt, "
    "relaxed unisex fit; two visible SHORT sleeves, ordinary hem, "
    "no hood, drawstrings, kangaroo pocket, or sweatshirt ribbed cuffs"
)
GARMENT_REJECT = (
    "hoodie", "hooded sweatshirt", "sweatshirt", "sweater",
    "pullover", "crewneck fleece", "long-sleeve shirt", "zip hoodie",
    "hood", "drawstrings", "kangaroo pocket", "ribbed sweatshirt cuffs",
    "fleece texture", "bulky winter garment",
)
GARMENT_QA = (
    "The upper body wears ONLY a real-looking Comfort Colors C1717 short-sleeve crew-neck T-shirt",
    "Two short sleeves terminate above the elbow; no sweatshirt cuffs or long sleeves",
    "No hood, drawstrings, kangaroo pocket, zip-front, fleece, sweater knit or sweatshirt body",
    "Same approved C1717 base color and garment across both takes and the final video",
    "Native Master artwork matches the approved SHA, original printed wording and flat ink",
    "Print stays at the CAM-approved front-center size and 150 mm below the lower collar rib",
)
# These patterns delete category/fulfillment SEO terms, NOT any Native Master art.
# Case-insensitive, bounded, English-centric. The full source title stays in DB.
_APPAREL_TERMS = re.compile(
    r"(?i)\b(?:comfort\s*colors?\s*(?:\u00ae\s*)?(?:c?1717)?|"
    r"gildan\s*(?:g?(?:500|180|185))?|"
    r"(?:c?1717|g?500|g?180|g?185)\b|"
    r"(?:unisex|oversized|heavyweight|garment\s*dyed|acid\s*wash)\s+"
    r"(?:t[\s-]*shirts?|tees?|shirts?)|"
    r"(?:hoodies|hoodie|sweatshirts?|sweaters?|pullovers?|"
    r"crewnecks?|jumpers?|long[\s-]*sleeves?|"
    r"t[\s-]*shirts?|tshirts?|tees?|shirts?|"
    r"menswear|womenswear|apparel|clothing))\b"
)
_SEO_NOISE = re.compile(
    r"(?i)\b(?:gift\s+(?:for|ideas?|guide)|"
    r"for\s+(?:her|him|men|women|mom|dad|wife|husband)|"
    r"personalized|customizable|made\s+to\s+order|"
    r"unisex|oversized|matching\s+outfits?|"
    r"trending\s+now)\b"
)
_CONTROLS = re.compile(r"[\x00-\x1f\x7f<>\{\}]")
_WHITESPACE = re.compile(r"\s+")
_CONFLICTING_APPAREL = re.compile(
    r"(?i)\b(?:hoodies|hoodie|sweatshirts?|sweaters?|pullovers?|"
    r"crewnecks?|long[\s-]*sleeves?|zip[\s-]*hoodies?)\b"
)
_UNSAFE_INSTRUCTIONS = re.compile(
    r"(?i)(?:<[^>]+>|ignore\s+(?:all\s+)?(?:previous\s+)?"
    r"(?:rules|instructions|system\s+prompt)|"
    r"system\s*:\s*|developer\s*:\s*|"
    r"\b(?:https?://|sudo\s+|curl\s+|rm\s+-rf)\b)"
)


def safe_render_title(source_title: str | None) -> str:
    """Short design cue, not original listing title or permission to change art.

    Prefer first informative Etsy title clause because later clauses mostly
    repeat SEO / alternate garments. Strip garment nouns in *all* clauses
    and avoid propagating prompt-like instructions or markup.
    """
    if not isinstance(source_title, str) or not source_title.strip():
        return "Original approved print artwork"
    if _UNSAFE_INSTRUCTIONS.search(source_title):
        return "Original approved print artwork"
    clauses = re.split(r"[,|;\n]+", source_title[:700])
    for raw in clauses[:8]:
        cleaned = _CONTROLS.sub(" ", raw)
        cleaned = _APPAREL_TERMS.sub(" ", cleaned)
        cleaned = _SEO_NOISE.sub(" ", cleaned)
        cleaned = _WHITESPACE.sub(" ", cleaned)
        cleaned = cleaned.strip(" -_./:;!?+&\"'\t")
        if len(cleaned) >= 4 and re.search(r"[A-Za-z0-9]", cleaned):
            return cleaned[:120].rstrip(" -_./:;")
    return "Original approved print artwork"


def render_context(*, title: str, artwork_sha256: str | None) -> dict:
    """Machine-readable authoritative garment and pre-upload QA contract."""
    render_title = safe_render_title(title)
    mismatch_risk = bool(_CONFLICTING_APPAREL.search(title or ""))
    return {
        "policyVersion": GARMENT_POLICY_VERSION,
        "garmentSku": GARMENT_SKU,
        "garmentName": GARMENT_NAME,
        "garmentType": "short_sleeve_crew_neck_tshirt",
        "construction": GARMENT_CONSTRUCTION,
        "designTheme": render_title,
        "renderTitle": render_title,
        "titleSource": "sanitized_marketplace_theme_only",
        "originalTitleRole": "seo_metadata_only_kept_in_CAM_not_in_render_prompt",
        "marketplaceTitleHasGarmentConflict": mismatch_risk,
        "garmentVisualQaPriority": "HIGH" if mismatch_risk else "NORMAL",
        "sourceOfTruth": "approved_CAM_C1717_garment_and_native_master",
        "artworkSource": "cam_approved_native_master_png",
        "artworkSha256": artwork_sha256,
        "marketplaceProductImageRole": "design_comparison_only_not_start_frame",
        "approvedStartFrameRole": "C1717_worn_hero_or_separately_generated_C1717_photo",
        "startFrameMustShowApprovedGarment": True,
        "colorPolicy": "approved_C1717_base_color_or_design_contrast_color",
        "decorationMethod": "flat_ink_print_only",
        "printPosition": "front_center",
        "firstInkBelowLowerCollarMm": 150,
        "rejectGarmentTypes": list(GARMENT_REJECT),
        "preUploadVisualQaRequired": True,
        "preUploadVisualQaChecks": list(GARMENT_QA),
        "preUploadVisualQaExecutor": "Muse_worker_visual_review_before_upload",
        "camServerCannotVerifyGarmentFromCodecAlone": True,
        "independentPostUploadVisualReviewStatus": "pending_review",
        "onGarmentMismatch": "GARMENT_MISMATCH_HOLD_do_not_upload_or_complete",
        "reRenderPolicy": "only_missing_aspect_after_new_authorized_canary",
        "renderInstruction": (
            "Create a real adult wearing exactly one Comfort Colors 1717 "
            "garment-dyed short-sleeve crew-neck T-SHIRT in a natural setting. "
            "DO NOT use an Etsy listing title, original product photo or SEO "
            "garment words to select clothing; marketplace photos can only "
            "help compare the print design. No sweatshirt, hoodie, sweater, "
            "long sleeves, hood, drawstrings, kangaroo pocket or ribbed cuffs. "
            "Place the exact approved Native Master FLAT-INK print front-center "
            "at CAM-approved physical scale, with first ink 150mm below the "
            "lower collar rib. Never invent lettering or alter source artwork. "
            "Use only an approved C1717 on-body start image or generate a new "
            "C1717 start image; block if no valid starting image can be made. "
            "Before upload, inspect garment, print and motion across the "
            "entire MP4. If garment differs: GARMENT_MISMATCH_HOLD."
        ),
    }