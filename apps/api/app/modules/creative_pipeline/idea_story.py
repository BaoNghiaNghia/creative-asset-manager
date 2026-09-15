from __future__ import annotations
import hashlib
import json
from typing import Any, Mapping
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

IDEA_STORY_SCHEMA_VERSION = 1
IDEA_STORY_SCHEMA_NAME = "creative_pipeline_idea_story_v1"
IDEA_STORY_PROMPT_TEMPLATE_VERSION = "idea_story_v1"

class StoryBeat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order: StrictInt = Field(gt=0)
    duration_hint: StrictStr
    action: StrictStr
    dialogue: StrictStr
    visual_note: StrictStr

class IdeaStory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idea_story_schema_version: StrictInt
    concept: StrictStr
    audience: StrictStr
    hook: StrictStr
    story_beats: list[StoryBeat] = Field(min_length=1)
    product_focus: list[StrictStr]
    must_show: list[StrictStr]
    must_avoid: list[StrictStr]
    tone: StrictStr
    platform_notes: StrictStr

    @classmethod
    def validate_document(cls, document: Mapping[str, Any]) -> "IdeaStory":
        if not isinstance(document, Mapping):
            raise ValueError("idea story must be an object")
        value = cls.model_validate(dict(document))
        if value.idea_story_schema_version != IDEA_STORY_SCHEMA_VERSION:
            raise ValueError("unsupported idea story schema version")
        return value

def idea_story_json_schema() -> dict[str, Any]:
    return IdeaStory.model_json_schema()

def canonical_json(document: Mapping[str, Any]) -> bytes:
    value = IdeaStory.validate_document(document).model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def assemble_idea_story_prompt(*, input_snapshot: Mapping[str, Any], knowledge_text: str, platform: str, required_aspect_ratios: list[str] | tuple[str, ...]) -> str:
    payload = {
        "template_version": IDEA_STORY_PROMPT_TEMPLATE_VERSION,
        "platform": platform,
        "required_aspect_ratios": list(required_aspect_ratios),
        "input_snapshot": input_snapshot,
        "knowledge": knowledge_text,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "Create one Idea Story only. Use only facts supported by the immutable Input Data. "
        "Do not invent product attributes, names, personalization, or marketing claims. "
        "Follow the Knowledge Pack and must_show/must_avoid constraints. "
        "Return exactly the strict creative_pipeline_idea_story_v1 JSON schema; do not return a Seedance, "
        "Google Omni, or video prompt.\n\n"
        "DETERMINISTIC CONTEXT:\n" + serialized
    )

def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
