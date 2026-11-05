"""Low-cost, source-specific Pinterest discovery for TikTokShop apparel.

This is deterministic and isolated from GatorHats. We derive audience/occasion
from the source filename and the already-saved visual embroidery evidence,
then pair it with verified garment placement. No additional model call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.realistic_review_ugc.apparel_placement import SourceApparelPlacement
from app.modules.realistic_review_ugc.seasonal_scout import detect_source_season


@dataclass(frozen=True)
class ApparelScoutIntent:
    family: str
    audience: str
    scene: str


# Order matters: specific family/occasion matches must win over "mama",
# "mrs", or general wedding/motherhood words in the same source.
_FAMILY_RULES: tuple[tuple[str, str, str, str], ...] = (
    (r"\b(?:godmother|god mom|godmom)\b", "godmother", "adult woman", "godmother family gift"),
    (r"\b(?:grandma|grandmother|granny|nana|memaw|gigi)\b", "grandma", "grandmother", "grandmother with family"),
    (r"\b(?:girl mama|girl mom|mama of girls)\b", "girl_mama", "mother with daughters", "mother daughter family"),
    (r"\b(?:boy mama|boy mom|mama of boys)\b", "boy_mama", "mother with sons", "mother son family"),
    (r"\b(?:mama goose|mother goose)\b", "mama_goose", "adult woman", "mother goose embroidered sweater"),
    (r"\b(?:dog mom|cat mom|pet mom|pet parent)\b", "pet_owner", "woman with pet", "pet parent lifestyle"),
    (r"\b(?:mr\.?\s*(?:and|&)\s*mrs\.?|mr[-_ ]and[-_ ]mrs|newlyweds?)\b", "couple_wedding", "newlywed couple", "matching couple"),
    (r"\b(?:engaged era|in my engaged|bride to be|fianc[ée]e?|engagement)\b", "engaged", "engaged woman", "bride to be casual"),
    (r"\b(?:mrs\.?\s+last\s+name|future mrs|mrs\.?\s+[a-z]+)\b", "bride", "newlywed woman", "bride wedding gift"),
    (r"\b(?:mom era|mom life|mama era|motherhood|mama|mom|mommy|mother)\b", "motherhood", "mother with children", "mom family home"),
    (r"\b(?:dad era|dad life|daddy|father|papa|grandpa|grandfather)\b", "fatherhood", "father", "dad family casual"),
    (r"\b(?:nurse|nursing|rn)\b", "nurse", "nurse", "nurse off duty casual"),
    (r"\b(?:teacher|kindergarten|educator)\b", "teacher", "teacher", "teacher casual outfit"),
    (r"\b(?:family|families|matching family)\b", "family", "family members", "family gathering"),
)

_GENERIC_NAME_WORDS = frozenset((
    "custom", "embroidered", "embroidery", "personalized", "sweatshirt",
    "sweater", "shirt", "shirts", "tee", "hoodie", "crewneck", "tshirt",
    "t", "woman", "women", "wearing", "photo", "style", "gift", "name",
    "names", "design", "graphic", "est", "new", "the", "with", "for",
    "and", "of", "my", "in", "a", "an", "on", "2024", "2025", "2026",
    "jpg", "jpeg", "png", "webp",
))


def source_apparel_intent(
    product_snapshot: dict | None,
    profile: dict | None = None,
) -> ApparelScoutIntent:
    product = dict(product_snapshot or {})
    source = dict(profile or {})
    visual = source.get("visual_context")
    if not isinstance(visual, dict):
        visual = {}
    # Filename is explicit user-supplied product identity. Visual text is next;
    # generic generated source descriptions deliberately do not vote.
    sections = [
        str(product.get("name") or ""),
        " ".join(str(value) for value in (visual.get("embroidery_text") or [])[:6]),
        str(visual.get("embroidery_identity") or ""),
        str(visual.get("summary") or ""),
    ]
    for text in sections:
        normalized = re.sub(r"[_-]+", " ", text).casefold()
        for pattern, family, audience, scene in _FAMILY_RULES:
            if re.search(pattern, normalized):
                return ApparelScoutIntent(family, audience, scene)
    # Unknown designs must not be mislabeled as mom/wedding from a broad
    # lifestyle caption. Use meaningful name tokens as a conservative anchor.
    filename = re.sub(r"[_-]+", " ", sections[0]).casefold()
    tokens = [
        token for token in re.findall(r"[a-z0-9]+", filename)
        if len(token) >= 3 and token not in _GENERIC_NAME_WORDS
    ][:3]
    if tokens:
        topic = " ".join(tokens)
        return ApparelScoutIntent("topic:" + "-".join(tokens), "person", topic)
    return ApparelScoutIntent("general", "person", "casual everyday")


def tiktokshop_semantic_queries(
    garment: str,
    placements: list[SourceApparelPlacement],
    product_snapshot: dict | None,
    profile: dict | None,
    *,
    scan_attempt_count: int = 0,
) -> list[str]:
    """Four distinct, placement-compatible discovery lanes per Windows claim.

    The Windows v58 runner uses only its first four search terms. Every lane
    therefore retains garment and audience; rotating later variants never
    sends the same generic sweatshirt query to unrelated product families.
    """
    intent = source_apparel_intent(product_snapshot, profile)
    zones = {placement.zone for placement in placements}
    chest = bool(zones & {"center_chest", "left_chest", "right_chest"})
    sleeves = bool(zones & {"left_sleeve", "right_sleeve", "left_upper_arm", "right_upper_arm"})
    collar = "collar" in zones
    pose = (
        "front chest and sleeves visible" if chest and sleeves
        else "front chest visible unobstructed" if chest
        else "three quarter sleeves visible" if sleeves
        else "front neckline visible" if collar
        else "front torso visible"
    )
    audience = intent.audience
    scene = intent.scene
    season = detect_source_season(product_snapshot, profile)
    if season:
        # Every lane must visually anchor the identified season. Pinterest
        # search otherwise returns generic sweatshirt photos even when the
        # source embroidery clearly depicts a Christmas scene, for example.
        variants = [
            f"{season.label} {audience} wearing {garment} {pose} candid photo",
            f"{season.scene} {audience} wearing {garment} {pose} lifestyle photo",
            f"{season.label} {season.decoration} {audience} {garment} photo",
            f"{season.label} {scene} {garment} {pose} indoor phone photo",
            f"{season.label} {audience} in {garment} {pose} decorated home photo",
            f"{season.label} {audience} {garment} {pose} family outdoor photo",
        ]
    else:
        # Keep the original no-season policy for designs without strong
        # artwork evidence; generic 'holiday' is not necessarily Christmas.
        variants = [
            f"{audience} wearing {garment} {pose} {scene} candid photo",
            f"{scene} {garment} {pose} natural lifestyle photo",
            f"{audience} {scene} wearing {garment} standing upper body photo",
            f"{scene} casual {garment} {pose} outdoor photo",
            f"{audience} in {garment} {pose} indoor casual phone photo",
            f"{scene} {garment} authentic lifestyle front view photo",
        ]
    if season:
        variants = [
            query if season.label.casefold() in query.casefold()
            else f"{season.label} {query}"
            for query in variants
        ]
    step = max(0, int(scan_attempt_count or 0))
    # Keep the best garment/subject lane stable, explore the other 3 lanes.
    rotated = [variants[0], *(
        variants[1:][step % 5:] + variants[1:][:step % 5]
    )]
    return list(dict.fromkeys(" ".join(query.split()) for query in rotated))[:4]


def rank_tiktokshop_queries_from_feedback(
    queries: list[str],
    feedback_rows: list[tuple[str, dict | None]],
) -> tuple[list[str], dict[str, float]]:
    """Rank existing query lanes using explicit human (v)/(x)/(AI) feedback.

    Feedback rows have already been tenant/workspace/family scoped by the
    repository. Two consistent human votes are required before changing
    priority, so a single opinion cannot starve an exploration lane.
    """
    from collections import Counter

    votes: dict[str, Counter] = {}
    for label, payload in feedback_rows:
        if label not in {"ref_good", "ref_bad"} or not isinstance(payload, dict):
            continue
        if payload.get("reference_feedback_kind") not in {"good", "bad", "ai"}:
            continue
        key = " ".join(str(payload.get("scout_query") or "").split()).casefold()
        if not key:
            continue
        votes.setdefault(key, Counter())[label] += 1

    scored: list[tuple[str, float, int]] = []
    for index, query in enumerate(queries):
        key = " ".join(query.split()).casefold()
        vote = votes.get(key, Counter())
        good, bad = vote["ref_good"], vote["ref_bad"]
        delta = (
            0.10 * (good - bad) / (good + bad + 2)
            if good + bad >= 2 else 0.0
        )
        # Prioritize the semantic/placement anchor until real feedback
        # justifies promoting a better-performing variant.
        score = round(max(0.70, min(0.99, 0.95 - index * 0.012 + delta)), 4)
        scored.append((query, score, index))
    scored.sort(key=lambda row: (-row[1], row[2]))
    return (
        [row[0] for row in scored],
        {" ".join(query.split()).casefold(): score for query, score, _ in scored},
    )
