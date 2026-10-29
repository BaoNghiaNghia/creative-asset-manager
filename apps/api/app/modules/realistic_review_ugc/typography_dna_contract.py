"""G3 deterministic validation boundary for Style DNA. No model calls or worker activation.

The analyzer must produce normalized relationships, not a pixel-accurate copy of
the reference. Reject untrusted or unsupported structure before persistence.
"""
from __future__ import annotations

import math
import re
from typing import Any

SCHEMA_VERSION = "typography-dna/v1"
ROLES = frozenset({"primary", "secondary"})
PATHS = frozenset({"straight", "arch", "curve"})
RELATIONS = frozenset({"above", "below", "overlap", "inside"})
ALIGNS = frozenset({"left", "center", "right"})
HEX_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class TypographyDnaContractError(ValueError):
    """Invalid analyzer result; must not be stored as ready DNA."""


def _text(value: Any, label: str, max_length: int = 120) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise TypographyDnaContractError(f"Invalid {label}")
    return value.strip()


def _number(value: Any, label: str, minimum: float = 0, maximum: float = 1) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypographyDnaContractError(f"Invalid {label}")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise TypographyDnaContractError(f"Out of range {label}")
    return number


def validate_typography_dna(payload: dict[str, Any], *, source_sha256: str) -> dict[str, Any]:
    """Return bounded, canonical DNA fields safe for a tenant-scoped write.

    Callers must fetch the source row with tenant scope, verify the source hash,
    enforce permissions, and store through the existing unique cache constraint.
    """
    if not isinstance(payload, dict) or not HEX_SHA256.fullmatch(source_sha256):
        raise TypographyDnaContractError("Invalid source")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise TypographyDnaContractError("Unsupported schema version")
    version = _text(payload.get("analyzer_version"), "analyzer_version", 64)
    if payload.get("image_hash") != source_sha256:
        raise TypographyDnaContractError("Source hash mismatch")
    region = payload.get("typography_region")
    if not isinstance(region, dict):
        raise TypographyDnaContractError("Missing typography region")
    bounds = {key: _number(region.get(key), key) for key in ("x", "y", "w", "h")}
    if bounds["w"] <= 0 or bounds["h"] <= 0 or bounds["x"] + bounds["w"] > 1.000001 or bounds["y"] + bounds["h"] > 1.000001:
        raise TypographyDnaContractError("Region exceeds image bounds")
    confidence = _number(payload.get("confidence"), "confidence")
    mood = payload.get("font_mood")
    if not isinstance(mood, dict):
        raise TypographyDnaContractError("Missing font mood")
    font_mood = {key: _text(mood.get(key), "font_mood." + key, 80) for key in ("primary", "weight", "contrast", "proportions")}
    secondary = mood.get("secondary")
    font_mood["secondary"] = _text(secondary, "font_mood.secondary", 80) if secondary is not None else None
    graph = payload.get("layout_graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise TypographyDnaContractError("Invalid layout graph")
    if not 1 <= len(graph["nodes"]) <= 12 or len(graph["edges"]) > 36:
        raise TypographyDnaContractError("Layout complexity limit")
    nodes = []
    ids = set()
    for item in graph["nodes"]:
        if not isinstance(item, dict):
            raise TypographyDnaContractError("Invalid layout node")
        node_id = _text(item.get("id"), "node id", 40)
        if node_id in ids:
            raise TypographyDnaContractError("Duplicate node")
        ids.add(node_id)
        role, path, align = item.get("role"), item.get("path_type"), item.get("align")
        if role not in ROLES or path not in PATHS or align not in ALIGNS:
            raise TypographyDnaContractError("Invalid layout node type")
        nodes.append({"id": node_id, "role": role, "path_type": path, "align": align,
                      "size_ratio": _number(item.get("size_ratio"), "size_ratio", 0.01, 10)})
    edges = []
    for item in graph["edges"]:
        if not isinstance(item, dict) or item.get("from") not in ids or item.get("to") not in ids or item["from"] == item["to"] or item.get("relation") not in RELATIONS:
            raise TypographyDnaContractError("Invalid layout edge")
        edges.append({"from": item["from"], "to": item["to"], "relation": item["relation"],
                      "strength": _number(item.get("strength"), "edge strength")})
    rules = payload.get("overlap_rules")
    if not isinstance(rules, list) or len(rules) > 24:
        raise TypographyDnaContractError("Invalid overlap rules")
    overlaps = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("a") not in ids or rule.get("b") not in ids or rule["a"] == rule["b"]:
            raise TypographyDnaContractError("Invalid overlap reference")
        overlaps.append({"a": rule["a"], "b": rule["b"],
                         "region": _text(rule.get("region"), "overlap region", 80),
                         "reading_priority": _text(rule.get("reading_priority"), "reading priority", 80),
                         "max_occlusion_ratio": _number(rule.get("max_occlusion_ratio"), "occlusion")})
    return {
        "schema_version": SCHEMA_VERSION, "analyzer_version": version, "image_hash": source_sha256,
        "typography_region": bounds, "style_family": _text(payload.get("style_family"), "style_family", 80),
        "font_mood": font_mood, "layout_graph": {"nodes": nodes, "edges": edges},
        "overlap_rules": overlaps, "confidence": confidence,
    }
