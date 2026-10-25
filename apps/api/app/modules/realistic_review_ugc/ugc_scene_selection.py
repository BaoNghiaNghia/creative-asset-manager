"""Deterministic, quality-filtered and diversified Scout references for UGC."""
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


def select_ugc_references(candidates, count=3):
    """Select 3 unique, diverse approved references; do not settle for fewer."""
    eligible = []
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
        eligible.append(item)
    eligible.sort(key=lambda c: (-(c.final_score or 0), c.id))
    picked = []
    signatures = set()
    for item in eligible:
        signature = item.diversity_signature or item.id
        if signature not in signatures:
            picked.append(item)
            signatures.add(signature)
        if len(picked) == count:
            break
    if len(picked) < count:
        for item in eligible:
            if item not in picked:
                picked.append(item)
            if len(picked) == count:
                break
    return picked if len(picked) == count else []
