"""Static storefront URL scope for a *future* authorized browser adapter.

No calls to HTTP, DNS, Playwright or /lease. Conservative path matching:
registered shop root and up to two descendant path components only; product
links outside the storefront tree require a separately reviewed adapter.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# Domain policy is intentionally narrower than marketplace-wide origins.
ALLOWED_HOSTS = {
    "etsy": frozenset({"www.etsy.com"}),
    "amazon": frozenset({
        "www.amazon.com", "amazon.com", "www.amazon.co.uk",
        "amazon.co.uk", "www.amazon.ca", "amazon.ca",
        "www.amazon.de", "amazon.de", "www.amazon.fr",
        "amazon.fr", "www.amazon.es", "amazon.es",
        "www.amazon.it", "amazon.it", "www.amazon.co.jp",
        "amazon.co.jp",
    }),
}
MAX_EXTRA_PATH_COMPONENTS = 2
MAX_PAGES_PER_PILOT = 5
MAX_ASSETS_PER_PILOT = 20
MAX_NAVIGATION_SECONDS = 20
MAX_REDIRECTS = 0
PATH_ALLOWED = re.compile(r"^/[A-Za-z0-9_.~/-]+$")


def _safe_parts(url: str) -> tuple[str, tuple[str, ...]] | None:
    if not isinstance(url, str) or not 12 <= len(url) <= 2048:
        return None
    # Reject percent escapes outright (including %2f, %2e, %5c) and
    # ambiguous encodings. Do not normalize or de-escape attacker-controlled
    # URLs in this scope decision.
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in url):
        return None
    if any(c in url for c in ("%", "\\", "@", ";", "?", "#")):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme != "https" or not host or parts.username is not None
                or parts.password is not None or parts.port is not None
                or parts.query or parts.fragment or parts.netloc != host
                or not PATH_ALLOWED.fullmatch(parts.path)):
            return None
    except (ValueError, UnicodeError):
        return None
    segments = tuple(parts.path.strip("/").split("/"))
    if (not 2 <= len(segments) <= 12
            or any(not segment or segment in (".", "..")
                   or len(segment) > 120 or ".." in segment for segment in segments)):
        return None
    return host, segments


def evaluate_storefront_url(
    *, platform: str, registered_url: str, candidate_url: str,
) -> tuple[bool, str]:
    """Pure fail-closed URL check; success is not permission to fetch."""
    registered = _safe_parts(registered_url)
    candidate = _safe_parts(candidate_url)
    if registered is None or registered[0] not in ALLOWED_HOSTS.get(platform, ()):
        return False, "registered_storefront_not_supported"
    if platform == "etsy":
        if len(registered[1]) != 2 or registered[1][0].lower() != "shop":
            return False, "registered_storefront_not_supported"
    elif platform == "amazon":
        if registered[1][0].lower() != "stores":
            return False, "registered_storefront_not_supported"
    else:
        return False, "platform_not_supported"
    if candidate is None:
        return False, "candidate_url_invalid"
    if candidate[0] != registered[0]:
        return False, "candidate_host_outside_shop"
    original = registered[1]
    requested = candidate[1]
    if requested[:len(original)] != original:
        return False, "candidate_path_outside_shop"
    if len(requested) - len(original) > MAX_EXTRA_PATH_COMPONENTS:
        return False, "candidate_path_depth_exceeded"
    return True, "registered_storefront_subpath"


def inactive_scan_budget() -> dict:
    """Display-only bounds; not a signed job or an executable capability."""
    return {
        "max_pages": MAX_PAGES_PER_PILOT,
        "max_gallery_items": MAX_ASSETS_PER_PILOT,
        "max_navigation_seconds": MAX_NAVIGATION_SECONDS,
        "max_redirects": MAX_REDIRECTS,
        "max_extra_path_components": MAX_EXTRA_PATH_COMPONENTS,
        "request_allowed": False,
    }
