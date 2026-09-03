from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from app.domain.providers.contracts import AiProviderError
from app.modules.video_search.proxy import PreparedVideoChunk
from app.providers.ai.gemini_video import (
    MEDIA_RESOLUTION_HIGH, MEDIA_RESOLUTION_LOW, GeminiVideoGeneration,
)


_ARRAY_FIELDS = ("actions", "objects", "people", "products", "locations", "visible_text", "styles", "colors", "moods", "keywords")
_STRING_FIELDS = ("summary", "visual_description", "speech")


class VideoAnalysisMode(str, Enum):
    FREE_SCAN = "free_scan"
    DETAIL_SCAN = "detail_scan"


@dataclass(frozen=True, slots=True)
class VideoTokenEstimate:
    video_tokens: int
    prompt_schema_tokens: int
    total_tokens: int
    duration_ms: int
    media_resolution: str


class VideoAnalysisTokenBudgetExceeded(RuntimeError):
    def __init__(self, estimate: VideoTokenEstimate, max_safe_tokens: int) -> None:
        self.estimated_tokens = estimate.total_tokens
        self.max_safe_tokens = max_safe_tokens
        self.duration_ms = estimate.duration_ms
        self.media_resolution = estimate.media_resolution
        super().__init__("Estimated video input tokens exceed the configured safe local limit.")


def estimate_video_input_tokens(
    *, duration_ms: int, media_resolution: str, prompt_schema_token_reserve: int = 8_000,
    low_tokens_per_second: int = 110, high_tokens_per_second: int = 320,
) -> VideoTokenEstimate:
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms <= 0:
        raise ValueError("duration_ms must be a positive integer")
    if media_resolution not in {MEDIA_RESOLUTION_LOW, MEDIA_RESOLUTION_HIGH}:
        raise ValueError("media_resolution is invalid")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (prompt_schema_token_reserve,)):
        raise ValueError("prompt_schema_token_reserve must be a non-negative integer")
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in (low_tokens_per_second, high_tokens_per_second)):
        raise ValueError("tokens per second must be positive integers")
    rate = low_tokens_per_second if media_resolution == MEDIA_RESOLUTION_LOW else high_tokens_per_second
    video_tokens = (duration_ms * rate + 999) // 1000
    return VideoTokenEstimate(video_tokens, prompt_schema_token_reserve, video_tokens + prompt_schema_token_reserve, duration_ms, media_resolution)




def build_video_search_metadata_schema(chunk_duration_ms: int) -> dict[str, Any]:
    if not isinstance(chunk_duration_ms, int) or isinstance(chunk_duration_ms, bool) or chunk_duration_ms <= 0:
        raise ValueError("chunk_duration_ms must be a positive integer")
    properties: dict[str, Any] = {
        "start_ms": {"type": "integer", "minimum": 0, "maximum": chunk_duration_ms},
        "end_ms": {"type": "integer", "minimum": 1, "maximum": chunk_duration_ms},
        "summary": {"type": "string"}, "visual_description": {"type": "string"},
        "speech": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    }
    properties.update({field: {"type": "array", "items": {"type": "string"}} for field in _ARRAY_FIELDS})
    segment = {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}
    return {"type": "object", "additionalProperties": False, "required": ["schema_version", "summary", "segments"], "properties": {"schema_version": {"type": "string", "enum": ["video-search-metadata-v1"]}, "summary": {"type": "string"}, "segments": {"type": "array", "items": segment}}}


def build_video_analysis_prompt(prompt_template: str, chunk_duration_ms: int) -> str:
    return f"{prompt_template}\n\nMandatory video evidence rules: describe only visible or audible evidence; do not invent identities, brands, objects, or speech. Transcribe visible text exactly as seen: do not autocomplete obscured text, correct spelling, translate, or invent unclear text. Use empty strings/lists when evidence is absent. Speech must be audible and uncertain speech must not be claimed as exact. Timestamps are relative to this proxy chunk, must stay within 0..{chunk_duration_ms}, and should form useful semantic scenes/events rather than frame-by-frame microsegments. Return JSON only according to the structured schema."


class GeminiVideoAnalyzer(Protocol):
    async def analyze_proxy(self, *, chunk: PreparedVideoChunk, prompt: str, response_json_schema: Mapping[str, Any], media_resolution: str) -> GeminiVideoGeneration: ...


@dataclass(frozen=True, slots=True)
class VideoSegmentMetadata:
    relative_start_ms: int; relative_end_ms: int; absolute_start_ms: int; absolute_end_ms: int
    summary: str; visual_description: str; actions: tuple[str, ...]; objects: tuple[str, ...]; people: tuple[str, ...]; products: tuple[str, ...]; locations: tuple[str, ...]; visible_text: tuple[str, ...]; speech: str; styles: tuple[str, ...]; colors: tuple[str, ...]; moods: tuple[str, ...]; keywords: tuple[str, ...]; confidence: float


@dataclass(frozen=True, slots=True)
class VideoChunkAnalysisResult:
    chunk_index: int; source_start_ms: int; source_end_ms: int; summary: str; segments: tuple[VideoSegmentMetadata, ...]; metadata_json: Mapping[str, Any]; provider: str; model: str | None; provider_request_id: str | None; usage_json: Mapping[str, Any]; provider_metadata_json: Mapping[str, Any]; analysis_mode: VideoAnalysisMode; media_resolution: str; estimated_input_tokens: int


class GeminiVideoAnalysisService:
    def __init__(self, client: GeminiVideoAnalyzer, *, max_safe_input_tokens: int | None = None) -> None:
        if max_safe_input_tokens is not None and (not isinstance(max_safe_input_tokens, int) or isinstance(max_safe_input_tokens, bool) or max_safe_input_tokens <= 0):
            raise ValueError("max_safe_input_tokens must be a positive integer or None")
        self._client = client
        self._max_safe_input_tokens = max_safe_input_tokens

    async def analyze_chunk(self, *, chunk: PreparedVideoChunk, prompt_template: str, mode: VideoAnalysisMode = VideoAnalysisMode.FREE_SCAN) -> VideoChunkAnalysisResult:
        if not isinstance(mode, VideoAnalysisMode):
            raise ValueError("mode must be a VideoAnalysisMode")
        resolution = MEDIA_RESOLUTION_LOW if mode is VideoAnalysisMode.FREE_SCAN else MEDIA_RESOLUTION_HIGH
        estimate = estimate_video_input_tokens(duration_ms=chunk.duration_ms, media_resolution=resolution)
        if self._max_safe_input_tokens is not None and estimate.total_tokens > self._max_safe_input_tokens:
            raise VideoAnalysisTokenBudgetExceeded(estimate, self._max_safe_input_tokens)
        schema = build_video_search_metadata_schema(chunk.duration_ms)
        generation = await self._client.analyze_proxy(chunk=chunk, prompt=build_video_analysis_prompt(prompt_template, chunk.duration_ms), response_json_schema=schema, media_resolution=resolution)
        return self._normalize(chunk, generation, mode, estimate)

    def _normalize(self, chunk: PreparedVideoChunk, generation: GeminiVideoGeneration, mode: VideoAnalysisMode, estimate: VideoTokenEstimate) -> VideoChunkAnalysisResult:
        document = generation.document
        self._root(document)
        segments = tuple(sorted((self._segment(chunk, value) for value in document["segments"]), key=lambda item: (item.relative_start_ms, item.relative_end_ms)))
        metadata = {"schema_version": "video-search-metadata-v1", "summary": document["summary"], "segments": [self._metadata_segment(item) for item in segments]}
        return VideoChunkAnalysisResult(chunk.chunk_index, chunk.source_start_ms, chunk.source_end_ms, document["summary"], segments, metadata, generation.provider, generation.model, generation.provider_request_id, generation.usage, {**dict(generation.provider_metadata), "analysis_mode": mode.value, "media_resolution": estimate.media_resolution, "estimated_input_tokens": estimate.total_tokens}, mode, estimate.media_resolution, estimate.total_tokens)

    @staticmethod
    def _fail(message: str) -> None:
        raise AiProviderError(message, code="gemini_video_invalid_metadata", retryable=False)

    def _root(self, document: Mapping[str, Any]) -> None:
        required = {"schema_version", "summary", "segments"}
        if not required.issubset(document) or document.get("schema_version") != "video-search-metadata-v1" or not isinstance(document.get("summary"), str) or not isinstance(document.get("segments"), list): self._fail("Gemini video metadata does not match the required schema.")

    def _segment(self, chunk: PreparedVideoChunk, value: Any) -> VideoSegmentMetadata:
        fields = {"start_ms", "end_ms", "summary", "visual_description", "speech", "confidence", *_ARRAY_FIELDS}
        if not isinstance(value, Mapping) or not fields.issubset(value): self._fail("Gemini video segment does not match the required schema.")
        start, end = value["start_ms"], value["end_ms"]
        if any(not isinstance(item, int) or isinstance(item, bool) for item in (start, end)) or start < 0 or end <= start or end > chunk.duration_ms: self._fail("Gemini video timestamps are invalid.")
        if any(not isinstance(value[field], str) for field in _STRING_FIELDS): self._fail("Gemini video string fields are invalid.")
        if any(not isinstance(value[field], list) or any(not isinstance(item, str) for item in value[field]) for field in _ARRAY_FIELDS): self._fail("Gemini video string arrays are invalid.")
        confidence = value["confidence"]
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not math.isfinite(confidence) or not 0 <= confidence <= 1: self._fail("Gemini video confidence is invalid.")
        absolute_start, absolute_end = chunk.source_start_ms + start, chunk.source_start_ms + end
        if absolute_start < chunk.source_start_ms or absolute_end > chunk.source_end_ms or absolute_end <= absolute_start: self._fail("Gemini video timestamps exceed the source chunk.")
        return VideoSegmentMetadata(start, end, absolute_start, absolute_end, value["summary"], value["visual_description"], *(tuple(value[field]) for field in _ARRAY_FIELDS[:6]), value["speech"], *(tuple(value[field]) for field in _ARRAY_FIELDS[6:]), float(confidence))

    @staticmethod
    def _metadata_segment(value: VideoSegmentMetadata) -> dict[str, Any]:
        return {"start_ms": value.absolute_start_ms, "end_ms": value.absolute_end_ms, "summary": value.summary, "visual_description": value.visual_description, "actions": list(value.actions), "objects": list(value.objects), "people": list(value.people), "products": list(value.products), "locations": list(value.locations), "visible_text": list(value.visible_text), "speech": value.speech, "styles": list(value.styles), "colors": list(value.colors), "moods": list(value.moods), "keywords": list(value.keywords), "confidence": value.confidence}
