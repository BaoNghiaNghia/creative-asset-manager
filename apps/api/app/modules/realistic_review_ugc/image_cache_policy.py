"""Private conditional caching for tenant-authorized RRUGC Drive previews.

Only the authenticated application may issue 304 responses. A media revision
always participates in the validator; different renditions never share an ETag.
This is browser/private-origin caching, not a public CDN authorization bypass.
"""
from __future__ import annotations

import hashlib


def preview_etag(
    tenant_id: str, remote_file_id: str, size: int, *, revision: str = ""
) -> str:
    material = "\x00".join((tenant_id, remote_file_id, str(size), revision))
    return '"' + hashlib.sha256(material.encode("utf-8")).hexdigest() + '"'


def preview_cache_control(
    requested_version: object,
    current_version: str,
    *,
    default_max_age: int = 300,
) -> str:
    # A long immutable browser cache is unsafe if tenant membership is
    # revoked. Always re-authorize within an hour, even with versioned URLs.
    if isinstance(requested_version, str) and requested_version == current_version:
        return "private, max-age=3600"
    return f"private, max-age={default_max_age}"


def matches_etag(candidate: object, etag: str) -> bool:
    if not isinstance(candidate, str):
        return False
    # Keep weak validators and arbitrary untrusted headers out of matching.
    return etag in (part.strip() for part in candidate.split(","))
