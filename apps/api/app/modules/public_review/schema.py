from __future__ import annotations

from typing import Any


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
