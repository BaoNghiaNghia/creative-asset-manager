from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from app.domain.providers.contracts import AiProviderError
from app.modules.video_search.analysis import (
    VideoAnalysisMode, VideoAnalysisTokenBudgetExceeded, GeminiVideoAnalysisService,
    build_video_analysis_prompt, build_video_search_metadata_schema, estimate_video_input_tokens,
)
from app.modules.video_search.proxy import PreparedVideoChunk
from app.providers.ai.gemini_video import MEDIA_RESOLUTION_HIGH, MEDIA_RESOLUTION_LOW, GeminiVideoGeneration


def _chunk() -> PreparedVideoChunk:
    path = Path(tempfile.gettempdir()) / "unused.mp4"
    return PreparedVideoChunk(2, path, 120000, 125000, 5000, 1, 640, 360)


def _segment(**changes):
    value = {"start_ms": 1000, "end_ms": 3500, "summary": "scene", "visual_description": "visible", "actions": [], "objects": [], "people": [], "products": [], "locations": [], "visible_text": [], "speech": "", "styles": [], "colors": [], "moods": [], "keywords": [], "confidence": 0.5}; value.update(changes); return value


class _Client:
    def __init__(self, document): self.document=document; self.calls=[]
    async def analyze_proxy(self, **kwargs): self.calls.append(kwargs); return GeminiVideoGeneration(self.document, "gemini", "m", "r", {"promptTokenCount": 7}, {})


class GeminiVideoAnalysisTest(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_sorts_and_allows_overlap(self):
        service = GeminiVideoAnalysisService(_Client({"schema_version":"video-search-metadata-v1", "summary":"all", "segments":[_segment(start_ms=2000,end_ms=5000), _segment(start_ms=0,end_ms=3000)]}))
        result=await service.analyze_chunk(chunk=_chunk(), prompt_template="Describe")
        self.assertEqual([(s.relative_start_ms,s.absolute_start_ms) for s in result.segments], [(0,120000),(2000,122000)])
        self.assertEqual(result.metadata_json["segments"][1]["end_ms"],125000)

    async def test_empty_segments_and_prompt_contract(self):
        result=await GeminiVideoAnalysisService(_Client({"schema_version":"video-search-metadata-v1","summary":"blank","segments":[]})).analyze_chunk(chunk=_chunk(), prompt_template="Describe")
        self.assertEqual(result.segments, ())
        prompt=build_video_analysis_prompt("Describe",5000).lower(); self.assertIn("exactly as seen",prompt); self.assertIn("do not autocomplete",prompt); self.assertIn("do not invent",prompt)

    async def test_rejects_invalid_schema_and_types(self):
        invalid=[{"schema_version":"wrong","summary":"x","segments":[]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":"no"}, {"schema_version":"video-search-metadata-v1","summary":1,"segments":[]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(end_ms=1000)]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(start_ms=True)]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(confidence=math.nan)]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(objects=[1])]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[{"start_ms":0}]}]
        for document in invalid:
            with self.subTest(document=document):
                with self.assertRaises(AiProviderError): await GeminiVideoAnalysisService(_Client(document)).analyze_chunk(chunk=_chunk(), prompt_template="x")

    async def test_ignores_unknown_root_and_segment_fields_but_keeps_normalized_contract(self):
        document = {
            "schema_version": "video-search-metadata-v1",
            "summary": "all",
            "provider_note": "safe extra field",
            "segments": [_segment(provider_note={"ignored": True})],
        }
        result = await GeminiVideoAnalysisService(_Client(document)).analyze_chunk(chunk=_chunk(), prompt_template="x")
        self.assertNotIn("provider_note", result.metadata_json)
        self.assertNotIn("provider_note", result.metadata_json["segments"][0])

    async def test_default_free_scan_and_explicit_detail_are_forwarded_once(self):
        client = _Client({"schema_version":"video-search-metadata-v1","summary":"x","segments":[]})
        service = GeminiVideoAnalysisService(client)
        free = await service.analyze_chunk(chunk=_chunk(), prompt_template="x")
        detail = await service.analyze_chunk(chunk=_chunk(), prompt_template="x", mode=VideoAnalysisMode.DETAIL_SCAN)
        self.assertEqual((free.analysis_mode, free.media_resolution), (VideoAnalysisMode.FREE_SCAN, MEDIA_RESOLUTION_LOW))
        self.assertEqual((detail.analysis_mode, detail.media_resolution), (VideoAnalysisMode.DETAIL_SCAN, MEDIA_RESOLUTION_HIGH))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["media_resolution"], MEDIA_RESOLUTION_LOW)
        self.assertEqual(client.calls[1]["media_resolution"], MEDIA_RESOLUTION_HIGH)
        self.assertEqual(free.usage_json, {"promptTokenCount": 7})

    async def test_budget_preflight_allows_low_twenty_minutes_and_rejects_high_before_call(self):
        document={"schema_version":"video-search-metadata-v1","summary":"x","segments":[]}
        long=PreparedVideoChunk(0, Path(tempfile.gettempdir()) / "unused.mp4", 0, 1200000, 1200000, 1, None, None)
        low_client=_Client(document); low=await GeminiVideoAnalysisService(low_client, max_safe_input_tokens=200000).analyze_chunk(chunk=long,prompt_template="x")
        self.assertLessEqual(low.estimated_input_tokens,200000); self.assertEqual(len(low_client.calls),1)
        high_client=_Client(document)
        with self.assertRaises(VideoAnalysisTokenBudgetExceeded): await GeminiVideoAnalysisService(high_client,max_safe_input_tokens=200000).analyze_chunk(chunk=long,prompt_template="x",mode=VideoAnalysisMode.DETAIL_SCAN)
        self.assertEqual(high_client.calls, [])

    def test_estimator_and_exact_boundary(self):
        low=estimate_video_input_tokens(duration_ms=1000,media_resolution=MEDIA_RESOLUTION_LOW)
        high=estimate_video_input_tokens(duration_ms=1000,media_resolution=MEDIA_RESOLUTION_HIGH)
        self.assertEqual((low.video_tokens,high.video_tokens),(110,320)); self.assertGreater(high.total_tokens,low.total_tokens)
        for bad in (0, True, -1):
            with self.assertRaises(ValueError): estimate_video_input_tokens(duration_ms=bad,media_resolution=MEDIA_RESOLUTION_LOW)
        with self.assertRaises(ValueError): estimate_video_input_tokens(duration_ms=1000,media_resolution="bad")
        with self.assertRaises(ValueError): estimate_video_input_tokens(duration_ms=1000,media_resolution=MEDIA_RESOLUTION_LOW,prompt_schema_token_reserve=-1)
        client=_Client({"schema_version":"video-search-metadata-v1","summary":"x","segments":[]})
        chunk=_chunk(); exact=estimate_video_input_tokens(duration_ms=chunk.duration_ms,media_resolution=MEDIA_RESOLUTION_LOW).total_tokens
        import asyncio
        asyncio.run(GeminiVideoAnalysisService(client,max_safe_input_tokens=exact).analyze_chunk(chunk=chunk,prompt_template="x"))
        self.assertEqual(len(client.calls),1)

    def test_schema_is_strict_and_dynamic(self):
        schema=build_video_search_metadata_schema(5000); self.assertFalse(schema["additionalProperties"]); segment=schema["properties"]["segments"]["items"]; self.assertFalse(segment["additionalProperties"]); self.assertEqual(segment["properties"]["end_ms"]["maximum"],5000)
