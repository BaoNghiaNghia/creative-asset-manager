from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlencode

VIDEO_SEGMENT_FIELDS = (
    "segments.summary^4",
    "segments.visual_description^3",
    "segments.speech^3",
    "segments.visible_text^3",
    "segments.actions^2",
    "segments.objects^2",
    "segments.people^2",
    "segments.products^2",
    "segments.locations",
    "segments.styles",
    "segments.colors",
    "segments.moods",
    "segments.keywords^2",
)


class VideoSearchResponseError(ValueError):
    pass


def _thumbnail_url(source: Mapping[str, Any]) -> str | None:
    source_type = source.get("source_type")
    external_source_id = source.get("external_source_id")
    external_asset_id = source.get("external_asset_id")
    provider = {"google_drive": "google-drive", "google-drive": "google-drive", "onedrive": "onedrive"}.get(source_type)
    if provider and isinstance(external_source_id, str) and isinstance(external_asset_id, str):
        query = urlencode({"provider": provider, "external_source_id": external_source_id, "fallback": "video"})
        return f"/api/explorer/thumbnail/{quote(external_asset_id, safe='')}?{query}"
    return source.get("thumbnail_url") if isinstance(source.get("thumbnail_url"), str) else None


def build_video_search_query(
    *,
    query: str,
    tenant_id: str,
    limit: int,
    external_source_id: str | None = None,
    allowed_source_asset_ids: set[str] | None = None,
    design_types: Sequence[str] = (),
) -> dict[str, Any]:
    nested_query = {
        "nested": {
            "path": "segments",
            "score_mode": "max",
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": list(VIDEO_SEGMENT_FIELDS),
                    "type": "best_fields",
                }
            },
            "inner_hits": {
                "name": "matching_segments",
                "size": limit,
                "_source": {
                    "includes": [
                        "segments.start_ms", "segments.end_ms", "segments.summary",
                        "segments.visual_description", "segments.speech",
                        "segments.visible_text", "segments.confidence",
                        "segments.actions", "segments.objects", "segments.people",
                        "segments.products", "segments.locations", "segments.styles",
                        "segments.colors", "segments.moods", "segments.keywords",
                    ]
                },
                "sort": [
                    {"_score": {"order": "desc"}},
                    {"segments.start_ms": {"order": "asc"}},
                    {"segments.end_ms": {"order": "asc"}},
                ],
            },
        }
    }
    filters: list[dict[str, Any]] = [{"term": {"tenant_id": tenant_id}}]
    if external_source_id:
        filters.append({"term": {"external_source_id": external_source_id}})
    normalized_design_types = sorted({value.strip().casefold() for value in design_types if value.strip()})
    if normalized_design_types:
        filters.append({"terms": {"design_type": normalized_design_types}})
    if allowed_source_asset_ids is not None:
        filters.append(
            {"terms": {"source_asset_id": sorted(allowed_source_asset_ids)}}
            if allowed_source_asset_ids
            else {"term": {"source_asset_id": "__no_authorized_video_assets__"}}
        )

    return {
        "size": limit,
        "track_total_hits": True,
        "_source": {
            "includes": [
                "source_asset_id",
                "analysis_run_id",
                "filename",
                "mime_type",
                "duration_ms",
                "source_type",
                "external_source_id",
                "external_asset_id",
                "web_url",
                "thumbnail_url",
            ]
        },
        "query": {
            "bool": {
                "filter": filters,
                "must": [nested_query],
                "should": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": ["summary^2", "filename^2"],
                            "type": "best_fields",
                        }
                    }
                ],
            }
        },
        "sort": [
            {"_score": {"order": "desc"}},
            {"analysis_run_id": {"order": "asc"}},
        ],
    }


def _number(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _match(hit: Mapping[str, Any]) -> dict[str, Any] | None:
    source = hit.get("_source")
    if not isinstance(source, Mapping):
        return None
    nested = source.get("segments")
    if isinstance(nested, Mapping):
        source = nested
    start_ms, end_ms = source.get("start_ms"), source.get("end_ms")
    if not isinstance(start_ms, int) or not isinstance(end_ms, int):
        return None
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "summary": source.get("summary") if isinstance(source.get("summary"), str) else "",
        "visual_description": source.get("visual_description") if isinstance(source.get("visual_description"), str) else "",
        "speech": source.get("speech") if isinstance(source.get("speech"), str) else "",
        "confidence": _number(source.get("confidence")),
        "score": _number(hit.get("_score")),
    }


def parse_video_search_response(response: Mapping[str, Any]) -> dict[str, Any]:
    hits_container = response.get("hits")
    if not isinstance(hits_container, Mapping):
        raise VideoSearchResponseError("Elasticsearch video response is malformed")
    raw_hits = hits_container.get("hits")
    if not isinstance(raw_hits, list):
        raise VideoSearchResponseError("Elasticsearch video hits are malformed")

    items: list[dict[str, Any]] = []
    for hit in raw_hits:
        if not isinstance(hit, Mapping):
            raise VideoSearchResponseError("Elasticsearch video hit is malformed")
        source = hit.get("_source")
        inner_hits = hit.get("inner_hits")
        if not isinstance(source, Mapping) or not isinstance(inner_hits, Mapping):
            raise VideoSearchResponseError("Elasticsearch video result is malformed")
        matching = inner_hits.get("matching_segments")
        nested_hits = matching.get("hits", {}).get("hits") if isinstance(matching, Mapping) else None
        if not isinstance(nested_hits, list):
            raise VideoSearchResponseError("Elasticsearch nested video matches are malformed")
        matches = [value for nested in nested_hits if isinstance(nested, Mapping) if (value := _match(nested)) is not None]
        if not matches:
            continue
        matches.sort(key=lambda value: (-value["score"], value["start_ms"], value["end_ms"]))
        best = matches[0]
        required = ("source_asset_id", "analysis_run_id", "filename", "mime_type")
        if any(not isinstance(source.get(field), str) for field in required):
            raise VideoSearchResponseError("Elasticsearch video source is malformed")
        items.append({
            "source_asset_id": source["source_asset_id"],
            "analysis_run_id": source["analysis_run_id"],
            "filename": source["filename"],
            "mime_type": source["mime_type"],
            "duration_ms": source.get("duration_ms") if isinstance(source.get("duration_ms"), int) else None,
            "source_type": source.get("source_type") if isinstance(source.get("source_type"), str) else None,
            "external_source_id": source.get("external_source_id") if isinstance(source.get("external_source_id"), str) else None,
            "external_asset_id": source.get("external_asset_id") if isinstance(source.get("external_asset_id"), str) else None,
            "web_url": source.get("web_url") if isinstance(source.get("web_url"), str) else None,
            "thumbnail_url": _thumbnail_url(source),
            "score": _number(hit.get("_score")),
            "best_match": best,
            "matches": matches,
        })

    total_value = hits_container.get("total", 0)
    total = total_value.get("value", 0) if isinstance(total_value, Mapping) else total_value
    if not isinstance(total, int):
        raise VideoSearchResponseError("Elasticsearch video total is malformed")
    return {"items": items, "total": total, "took_ms": response.get("took") if isinstance(response.get("took"), int) else None}
