"""Safe links back to files in their connected cloud provider."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from app.providers.microsoft.onedrive_mapper import parse_item_id


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
    if provider in {"onedrive", "one-drive"}:
        return (
            host == "onedrive.live.com"
            or host.endswith(".sharepoint.com")
            or host.endswith(".sharepoint-df.com")
            or host == "my.microsoftpersonalcontent.com"
            or host.endswith(".microsoftpersonalcontent.com")
            or host == "1drv.ms"
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


def _is_onedrive_root_url(value: str) -> bool:
    """Whether a saved OneDrive URL only opens the account landing page."""
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "onedrive.live.com":
        return parsed.path in {"", "/"} and not parsed.query
    if host.endswith(".microsoftpersonalcontent.com"):
        return parsed.path.rstrip("/").endswith("/Documents") and not parsed.query
    return False


def _onedrive_item_url(external_asset_id: str) -> str | None:
    """Build a consumer OneDrive file link from our validated composite ID."""
    try:
        drive_id, item_id = parse_item_id(external_asset_id.strip())
    except ValueError:
        return None
    return "https://onedrive.live.com/?" + urlencode({"cid": drive_id, "id": item_id})


def resolve_source_web_url(
    *,
    provider: str,
    external_asset_id: str,
    source_metadata: Mapping[str, object] | None,
) -> str | None:
    """Resolve a provider URL without trusting arbitrary persisted values."""

    metadata = source_metadata if isinstance(source_metadata, Mapping) else {}
    fallback_onedrive_root_url = None
    for key in ("web_url", "webViewLink", "webUrl", "source_web_url"):
        resolved = _safe_https_url(provider, metadata.get(key))
        if resolved:
            if _provider_name(provider) in {"onedrive", "one-drive"} and _is_onedrive_root_url(resolved):
                fallback_onedrive_root_url = resolved
                continue
            return resolved
    if _provider_name(provider) in {"onedrive", "one-drive"}:
        return _onedrive_item_url(external_asset_id) or fallback_onedrive_root_url
    if _provider_name(provider) in {"google-drive", "google"} and external_asset_id.strip():
        return f"https://drive.google.com/open?id={quote(external_asset_id.strip(), safe='')}"
    return None
