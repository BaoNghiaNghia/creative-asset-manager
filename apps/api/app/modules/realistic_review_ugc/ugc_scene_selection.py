"""Diversified, design-specific Scout references for UGC with durable picks."""
from hashlib import sha256
from random import Random
from urllib.parse import urlsplit

APPROVED_STATUSES = frozenset({"approved", "import_queued", "importing", "drive_ready", "import_failed"})


def allowed_scout_image_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and
                not parsed.password and parsed.port in (None, 443) and
                (host == "pinimg.com" or host.endswith(".pinimg.com")))
    except ValueError:
        return False


def eligible_ugc_references(candidates, *, limit=20):
    """The same approved, safe, deduplicated pool used by the UI and generator.

    The caller provides the highest-scored 150 candidates; the generator must
    choose from the exact 20 images actually visible in the Slot 6 gallery.
    """
    unique = []
    seen = set()
    for item in candidates:
        if (item.status not in APPROVED_STATUSES or not item.image_url or
                not allowed_scout_image_url(item.image_url) or
                item.head_visible is False or item.people_count == 0 or
                (item.mobile_ugc_score is not None and item.mobile_ugc_score < 0.55)):
            continue
        flags = item.ai_signal_json if isinstance(item.ai_signal_json, dict) else {}
        if flags.get("reference_manual_label") in ("bad", "ai"):
            continue
        identity = item.content_hash or item.image_url.strip().casefold()
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
        if len(unique) == limit:
            break
    return unique


def select_ugc_references(candidates, count=3, *, selection_key=""):
    """Sample truly diverse references by design, not top-scored first three.

    The seed is design/revision scoped. The same design gets the same randomized
    pick on resumed requests, while different designs get independent samples.
    Once queued, the processing job pins each selected image for all retries.
    Good human feedback has priority and bad/AI feedback is never eligible.
    """
    pool = eligible_ugc_references(candidates)
    if len(pool) < count:
        return []

    # Stable across database query order changes; never use Python's salted hash().
    seed_material = selection_key + ":" + ",".join(sorted(c.id for c in pool))
    rng = Random(int.from_bytes(sha256(seed_material.encode("utf-8")).digest(), "big"))
    good = [c for c in pool if (c.ai_signal_json or {}).get("reference_manual_label") == "good"]
    ordinary = [c for c in pool if c not in good]
    good.sort(key=lambda c: c.id)
    ordinary.sort(key=lambda c: c.id)
    rng.shuffle(good)
    rng.shuffle(ordinary)
    choices = good + ordinary

    picked = []
    signatures = set()
    for item in choices:
        signature = item.diversity_signature or item.id
        if signature in signatures:
            continue
        picked.append(item)
        signatures.add(signature)
        if len(picked) == count:
            return picked
    for item in choices:
        if item not in picked:
            picked.append(item)
        if len(picked) == count:
            return picked
    return []
