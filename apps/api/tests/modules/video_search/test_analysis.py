from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from app.domain.providers.contracts import AiProviderError
from app.modules.video_search.analysis import GeminiVideoAnalysisService, build_video_analysis_prompt, build_video_search_metadata_schema
from app.modules.video_search.proxy import PreparedVideoChunk
from app.providers.ai.gemini_video import GeminiVideoGeneration


def _chunk() -> PreparedVideoChunk:
    path = Path(tempfile.gettempdir()) / "unused.mp4"
    return PreparedVideoChunk(2, path, 120000, 125000, 5000, 1, 640, 360)


def _segment(**changes):
    value = {"start_ms": 1000, "end_ms": 3500, "summary": "scene", "visual_description": "visible", "actions": [], "objects": [], "people": [], "products": [], "locations": [], "visible_text": [], "speech": "", "styles": [], "colors": [], "moods": [], "keywords": [], "confidence": 0.5}; value.update(changes); return value


class _Client:
    def __init__(self, document): self.document=document
    async def analyze_proxy(self, **kwargs): return GeminiVideoGeneration(self.document, "gemini", "m", "r", {}, {})


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
        invalid=[{"schema_version":"wrong","summary":"x","segments":[]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":"no"}, {"schema_version":"video-search-metadata-v1","summary":1,"segments":[]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(end_ms=1000)]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(start_ms=True)]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(confidence=math.nan)]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(objects=[1])]}, {"schema_version":"video-search-metadata-v1","summary":"x","segments":[_segment(unknown=True)]}]
        for document in invalid:
            with self.subTest(document=document):
                with self.assertRaises(AiProviderError): await GeminiVideoAnalysisService(_Client(document)).analyze_chunk(chunk=_chunk(), prompt_template="x")

    def test_schema_is_strict_and_dynamic(self):
        schema=build_video_search_metadata_schema(5000); self.assertFalse(schema["additionalProperties"]); segment=schema["properties"]["segments"]["items"]; self.assertFalse(segment["additionalProperties"]); self.assertEqual(segment["properties"]["end_ms"]["maximum"],5000)
