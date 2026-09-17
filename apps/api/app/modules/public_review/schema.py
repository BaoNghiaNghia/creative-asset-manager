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

_ALLOWED_NODES = {"doc", "paragraph", "heading", "text", "hardBreak", "bulletList", "orderedList", "listItem", "taskList", "taskItem", "blockquote", "horizontalRule"}
_ALLOWED_MARKS = {"bold", "italic", "strike", "code", "link"}
def validate_annotation_document(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "doc" or not isinstance(value.get("content", []), list): raise ValueError("invalid annotation document")
    nodes = 0
    def walk(node, depth=0):
        nonlocal nodes
        if not isinstance(node, dict) or depth > 20: raise ValueError("invalid annotation document")
        nodes += 1
        if nodes > 500 or node.get("type") not in _ALLOWED_NODES: raise ValueError("invalid annotation document")
        if node.get("type") == "heading" and node.get("attrs", {}).get("level") not in {1,2,3}: raise ValueError("invalid annotation document")
        if node.get("type") == "text" and not isinstance(node.get("text"), str): raise ValueError("invalid annotation document")
        for mark in node.get("marks", []):
            if not isinstance(mark, dict) or mark.get("type") not in _ALLOWED_MARKS: raise ValueError("invalid annotation document")
            if mark.get("type") == "link":
                href=str(mark.get("attrs", {}).get("href", ""))
                if not href.startswith(("http://", "https://")) or len(href)>2048: raise ValueError("invalid annotation document")
        for child in node.get("content", []): walk(child, depth+1)
    walk(value)
    if len(str(value).encode()) > 100_000 or len(extract_plain_text(value)) > 10_000: raise ValueError("invalid annotation document")
    return value
