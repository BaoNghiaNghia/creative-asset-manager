"""Resolve the Pinterest lifestyle photo(s) that guided a Stage 4 hat-swap.

The persisted reference snapshot is authoritative. It is deliberately *not*
the product/source hat snapshot: Stage 5 compares the Pinterest lifestyle
reference before replacement to the completed generated UGC after replacement.

For multiple input references, expose an ordered choice instead of pretending
that a single reference was proven to be the base scene.
"""
from __future__ import annotations

from app.modules.realistic_review_ugc.model import RrugcStage2JobModel


def stage5_before_references(
    job: RrugcStage2JobModel,
) -> list[tuple[str, str | None, int | None]]:
    snapshot = job.selected_reference_snapshot_json
    if not isinstance(snapshot, list):
        return []
    selected: list[tuple[int, int, str, str | None, int | None]] = []
    seen_remote_ids: set[str] = set()
    for original_index, item in enumerate(snapshot):
        if not isinstance(item, dict):
            continue
        remote_id = str(item.get("remote_file_id") or "").strip()
        if not remote_id or remote_id in seen_remote_ids:
            continue
        seen_remote_ids.add(remote_id)
        # position records the order delivered to the image-generation model;
        # malformed legacy values retain their original list order.
        try:
            order = int(item.get("position", original_index))
        except (ValueError, TypeError):
            order = original_index
        kind = str(item.get("content_type") or "").strip() or None
        if kind and not kind.startswith("image/"):
            continue
        size = item.get("size_bytes")
        if not isinstance(size, int) or size < 0:
            size = None
        selected.append((order, original_index, remote_id, kind, size))
    selected.sort(key=lambda row: (row[0], row[1]))
    # Stage 4 currently supplies at most 3 Pinterest inputs.
    return [(remote_id, kind, size) for _, _, remote_id, kind, size in selected[:3]]
