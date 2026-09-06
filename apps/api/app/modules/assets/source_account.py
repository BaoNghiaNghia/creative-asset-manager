from __future__ import annotations

from typing import Any


def source_account_username(metadata: dict[str, Any] | None, connection_email: str | None = None) -> str | None:
    """Return the safe display email for a connected external source."""
    values = metadata if isinstance(metadata, dict) else {}
    for value in (values.get("account_email"), values.get("email"), connection_email):
        text = str(value or "").strip()
        if text:
            return text
    return None
