import asyncio
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.modules.creative_pipeline.idea_story import (
    IDEA_STORY_PROMPT_TEMPLATE_VERSION,
    IdeaStory,
    assemble_idea_story_prompt,
    idea_story_json_schema,
)
from app.providers.ai.openai import OpenAiMetadataProvider
from app.domain.providers.contracts import AiStructuredTextInput

def valid_doc():
    return {
        "idea_story_schema_version": 1, "concept": "c", "audience": "a", "hook": "h",
        "story_beats": [{"order": 1, "duration_hint": "0-2s", "action": "show", "dialogue": "", "visual_note": "product"}],
        "product_focus": ["item"], "must_show": ["item"], "must_avoid": [], "tone": "warm", "platform_notes": "etsy",
    }

def test_idea_schema_strict_and_prompt_deterministic():
    value = IdeaStory.validate_document(valid_doc())
    assert value.model_dump()["idea_story_schema_version"] == 1
    with pytest.raises(Exception): IdeaStory.validate_document({**valid_doc(), "unexpected": 1})
    with pytest.raises(Exception): IdeaStory.validate_document({**valid_doc(), "story_beats": [{**valid_doc()["story_beats"][0], "order": 0}]})
    prompt_a = assemble_idea_story_prompt(input_snapshot={"b": 2, "a": 1}, knowledge_text="K", platform="etsy", required_aspect_ratios=["1:1"])
    prompt_b = assemble_idea_story_prompt(input_snapshot={"a": 1, "b": 2}, knowledge_text="K", platform="etsy", required_aspect_ratios=["1:1"])
    assert prompt_a == prompt_b and IDEA_STORY_PROMPT_TEMPLATE_VERSION in prompt_a
    assert idea_story_json_schema()["additionalProperties"] is False

class FakeResponses:
    async def create(self, **request):
        self.request = request
        return SimpleNamespace(
            id="resp-1", model="configured-model", status="completed",
            output=[SimpleNamespace(content=[SimpleNamespace(type="output_text", text=json.dumps(valid_doc()))])],
            usage=SimpleNamespace(input_tokens=2, output_tokens=3, total_tokens=5),
        )

class FakeClient:
    def __init__(self): self.responses = FakeResponses()

def test_structured_openai_is_text_only_strict_and_idempotent():
    client = FakeClient()
    provider = OpenAiMetadataProvider(api_key="x", model="configured-model", allowed_models=("configured-model",), client=client)
    result = asyncio.run(provider.generate_structured(AiStructuredTextInput(
        tenant_id="tenant-a", prompt="prompt", json_schema=idea_story_json_schema(),
        schema_name="creative_pipeline_idea_story_v1", idempotency_key="creative_pipeline:idea_story:n:v001",
    )))
    assert result.document["idea_story_schema_version"] == 1
    request = client.responses.request
    assert request["input"][0]["content"][0]["type"] == "input_text"
    assert "input_image" not in json.dumps(request)
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["name"] == "creative_pipeline_idea_story_v1"
    assert request["extra_headers"]["Idempotency-Key"].endswith(":v001")
