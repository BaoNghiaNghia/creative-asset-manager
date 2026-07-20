from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "[redacted-url]"


def redact_url_queries(text: str | None) -> str | None:
    if text is None:
        return None
    return _URL_PATTERN.sub(lambda match: redact_url(match.group(0)), text)


def sanitize_sensitive_urls(value):
    """Return a copy with URL credentials/query/fragment removed recursively."""
    if isinstance(value, dict):
        return {str(key): sanitize_sensitive_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_sensitive_urls(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_sensitive_urls(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("https://", "http://")):
        return redact_url(value)
    return value


def sanitize_log_value(value):
    if isinstance(value, dict):
        return {str(key): sanitize_log_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_log_value(item) for item in value]
    if isinstance(value, str):
        return redact_url_queries(value)
    return value
