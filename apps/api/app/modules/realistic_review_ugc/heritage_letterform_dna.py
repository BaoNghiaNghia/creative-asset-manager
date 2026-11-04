"""Deterministic, source-text-free letterform traits from approved G3 structure.

These are design *hints*, not proof of a particular font, swash, or stitch.
Only call after effective_reviewed_dna verifies rights and human approval.
"""
from __future__ import annotations
from typing import Any, Mapping


def derive_letterform_traits(reviewed: Mapping[str, Any]) -> dict[str, Any]:
    mood = reviewed.get("font_mood") or {}
    graph = reviewed.get("layout_graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    valid_types = {"serif", "sans_serif", "slab_serif", "script", "handwritten", "display", "monospace"}
    primary = mood.get("primary")
    secondary = mood.get("secondary")
    family = primary if primary in valid_types else "unknown"
    mixed = secondary in valid_types and secondary != primary
    arch = any(node.get("path_type") in ("arch", "curve") for node in nodes if isinstance(node, dict))
    overlap = any(edge.get("relation") == "overlap" for edge in edges if isinstance(edge, dict))
    size_ratios = [
        node.get("size_ratio") for node in nodes if isinstance(node, dict)
        and isinstance(node.get("size_ratio"), (int, float)) and not isinstance(node.get("size_ratio"), bool)
        and 0 < node["size_ratio"] <= 10
    ]
    hierarchy = max(size_ratios) / min(size_ratios) if len(size_ratios) > 1 else 1
    tags: list[str] = []
    if family in {"serif", "slab_serif"}:
        tags.append("heritage_serif_candidate")
    if family in {"script", "handwritten"} or secondary in {"script", "handwritten"}:
        tags.append("expressive_script_candidate")
    if mixed:
        tags.append("type_family_contrast")
    if arch:
        tags.append("curved_baseline")
    if overlap:
        tags.append("layered_type")
    if hierarchy >= 1.8:
        tags.append("strong_hierarchy")
    return {
        "version": "letterform-traits-h1",
        "primary_family": family,
        "mixed_families": bool(mixed),
        "curved_baseline": bool(arch),
        "overlap": bool(overlap),
        "strong_hierarchy": hierarchy >= 1.8,
        "style_tags": tags,
        "custom_glyphs_verified": False,
        "embroidery_verified": False,
    }
