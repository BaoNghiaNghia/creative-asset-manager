"""Safe links back to files in their connected cloud provider."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote, urlsplit, urlunsplit


def _provider_name(provider: str) -> str:
    return provider.strip().lower().replace("_", "-")


def _allowed_host(provider: str, hostname: str) -> bool:
    provider = _provider_name(provider)
    host = hostname.lower().rstrip(".")
    if provider in {"google-drive", "google"}:
        return host in {"drive.google.com", "docs.google.com"}
    if provider in {"sharepoint", "microsoft", "microsoft-sharepoint"}:
        return (
            host.endswith(".sharepoint.com")
            or host.endswith(".sharepoint-df.com")
            or host == "office.com"
            or host.endswith(".office.com")
            or host == "microsoft365.com"
            or host.endswith(".microsoft365.com")
        )
    return False


def _safe_https_url(provider: str, value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        if not _allowed_host(provider, parsed.hostname):
            return None
        return urlunsplit(("https", parsed.hostname.lower(), parsed.path or "/", parsed.query, ""))
    except ValueError:
        return None


def resolve_source_web_url(
    *,
    provider: str,
    external_asset_id: str,
    source_metadata: Mapping[str, object] | None,
) -> str | None:
    """Resolve a provider URL without trusting arbitrary persisted values."""

    metadata = source_metadata if isinstance(source_metadata, Mapping) else {}
    for key in ("web_url", "webViewLink", "webUrl", "source_web_url"):
        resolved = _safe_https_url(provider, metadata.get(key))
        if resolved:
            return resolved
    if _provider_name(provider) in {"google-drive", "google"} and external_asset_id.strip():
        return f"https://drive.google.com/open?id={quote(external_asset_id.strip(), safe='')}"
    return None
