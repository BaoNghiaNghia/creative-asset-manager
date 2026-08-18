from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.modules.assets.model import SourceAssetModel
from app.modules.video_search.model import VideoAnalysisChunkModel, VideoAnalysisRunModel


class VideoIndexDataError(ValueError):
    pass


def video_segment_mapping() -> dict[str, Any]:
    text = {"type": "text", "analyzer": "cam_text_v2"}
    keyword = {"type": "keyword"}
    return {
        "type": "nested",
        "properties": {
            "start_ms": {"type": "long"}, "end_ms": {"type": "long"},
            "confidence": {"type": "float"}, "summary": text,
            "visual_description": text, "speech": text, "visible_text": text,
            "keywords": keyword, "actions": keyword, "objects": keyword,
            "people": keyword, "products": keyword, "locations": keyword,
            "styles": keyword, "colors": keyword, "moods": keyword,
        },
    }


def video_index_mapping() -> dict[str, Any]:
    keyword = {"type": "keyword"}
    text = {"type": "text", "analyzer": "cam_text_v2"}
    return {"dynamic": "strict", "properties": {
        "tenant_id": keyword, "source_asset_id": keyword, "source_fingerprint": keyword,
        "video_metadata_profile_id": keyword, "metadata_profile": keyword,
        "metadata_profile_version": keyword, "prompt_version": keyword,
        "analysis_version": keyword, "ai_provider": keyword, "ai_model": keyword,
        "duration_ms": {"type": "long"}, "summary": text, "source_type": keyword,
        "external_source_id": keyword, "external_asset_id": keyword, "filename": text,
        "mime_type": keyword, "web_url": keyword, "thumbnail_url": keyword,
        "analysis_run_id": keyword, "analysis_completed_at": {"type": "date"},
        "segments": video_segment_mapping(),
    }}


def video_document_id(run: VideoAnalysisRunModel) -> str:
    values = (run.tenant_id, run.source_asset_id, run.source_fingerprint,
              run.video_metadata_profile_id, run.metadata_profile_version,
              run.prompt_version, run.analysis_version, run.ai_provider or "")
    return "video:" + hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def build_video_document(*, run: VideoAnalysisRunModel, source: SourceAssetModel, chunks: Sequence[VideoAnalysisChunkModel]) -> dict[str, Any]:
    if run.status != "completed" or run.completed_chunks != run.total_chunks:
        raise VideoIndexDataError("completed video run is required")
    if source.tenant_id != run.tenant_id or source.id != run.source_asset_id:
        raise VideoIndexDataError("source asset does not belong to completed run")
    completed = [chunk for chunk in chunks if chunk.status == "completed"]
    if len(completed) != run.total_chunks or len(completed) != len(chunks):
        raise VideoIndexDataError("all video chunks must be completed")
    segments = []
    for chunk in completed:
        metadata = chunk.metadata_json if isinstance(chunk.metadata_json, Mapping) else {}
        values = metadata.get("segments", [])
        if not isinstance(values, list):
            raise VideoIndexDataError("chunk segments must be a list")
        for value in values:
            if not isinstance(value, Mapping):
                raise VideoIndexDataError("video segment must be an object")
            start, end = value.get("start_ms"), value.get("end_ms")
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or (run.duration_ms is not None and end > run.duration_ms):
                raise VideoIndexDataError("invalid persisted video segment range")
            segments.append(dict(value))
    segments.sort(key=lambda segment: (segment["start_ms"], segment["end_ms"]))
    metadata = source.source_metadata if isinstance(source.source_metadata, Mapping) else {}
    return {
        "_id": video_document_id(run), "tenant_id": run.tenant_id,
        "source_asset_id": source.id, "source_fingerprint": run.source_fingerprint,
        "video_metadata_profile_id": run.video_metadata_profile_id,
        "metadata_profile": run.metadata_profile, "metadata_profile_version": run.metadata_profile_version,
        "prompt_version": run.prompt_version, "analysis_version": run.analysis_version,
        "ai_provider": run.ai_provider, "ai_model": run.ai_model, "duration_ms": run.duration_ms,
        "summary": (run.summary_json or {}).get("summary", ""), "source_type": metadata.get("source_type", "google_drive"),
        "external_source_id": source.external_source_id, "external_asset_id": source.external_asset_id,
        "filename": source.filename or "", "mime_type": source.mime_type or "",
        "web_url": metadata.get("web_url") or metadata.get("webViewLink"), "thumbnail_url": metadata.get("thumbnail_url"),
        "analysis_run_id": run.id, "analysis_completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "segments": segments,
    }
