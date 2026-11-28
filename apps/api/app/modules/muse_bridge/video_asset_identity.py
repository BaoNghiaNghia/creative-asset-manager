"""Stable legacy IDs and isolated, SHA-derived replacement storage identities."""
import hashlib
import re


def video_asset_id(listing, artwork_sha, aspect, revision=None):
    if (not re.fullmatch(r"[0-9]{5,20}", listing)
            or not re.fullmatch(r"[0-9a-f]{64}", artwork_sha)
            or aspect not in ("vertical", "horizontal")):
        raise ValueError("Invalid UGC asset identity")
    base = f"muse-ugc:{listing}:{artwork_sha}:{aspect}"
    if revision is None:
        return base
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{32}", revision):
        raise ValueError("Invalid UGC storage revision")
    # Keep Google Drive appProperties below their per-entry byte limit.
    return "muse-ugc-revision:" + hashlib.sha256((base + ":" + revision).encode()).hexdigest()
