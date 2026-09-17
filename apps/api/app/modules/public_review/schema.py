from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ShareScopeInput(BaseModel):
    external_source_id: str = Field(min_length=1, max_length=36)
    folder_external_id: str = Field(min_length=1, max_length=2048)
    folder_name: str | None = Field(default=None, max_length=1024)


class CreateShareRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[ShareScopeInput] = Field(min_length=1, max_length=100)
    allow_comments: bool = True
    allow_download: bool = False
    expires_at: datetime | None = None


class UpdateShareRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    scopes: list[ShareScopeInput] | None = Field(default=None, min_length=1, max_length=100)
    allow_comments: bool | None = None
    allow_download: bool | None = None
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value



def extract_plain_text(content: dict[str, Any]) -> str:
    """Extract bounded text from already-validated editor JSON; Phase 1 has no HTTP schema."""
    if not isinstance(content, dict):
        raise ValueError("annotation content must be an object")
    parts: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                parts.append(text)
            for child in node.get("content", []):
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(content)
    result = " ".join(" ".join(parts).split())
    if len(result) > 10000:
        raise ValueError("annotation text is too long")
    return result
