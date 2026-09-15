from __future__ import annotations
import hashlib, json
from typing import Any, Mapping
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, Field
from app.modules.creative_pipeline.platforms import PlatformProfile

PROMPT_SCHEMA_VERSION=1
PROMPT_DRAFT_SCHEMA_VERSION=1
SEEDANCE_PROMPT_TEMPLATE_VERSION="seedance_prompt_v1"
GOOGLE_OMNI_PROMPT_TEMPLATE_VERSION="google_omni_prompt_v1"

class PromptDraftOutput(BaseModel):
    model_config=ConfigDict(extra="forbid", strict=True)
    aspect_ratio: StrictStr
    prompt: StrictStr = Field(min_length=1)

class PromptDraft(BaseModel):
    model_config=ConfigDict(extra="forbid", strict=True)
    prompt_draft_schema_version: StrictInt
    outputs: list[PromptDraftOutput] = Field(min_length=1)

class PromptOutput(BaseModel):
    model_config=ConfigDict(extra="forbid", strict=True)
    provider: StrictStr
    model: StrictStr
    aspect_ratio: StrictStr
    prompt: StrictStr = Field(min_length=1)
    generation_parameters: dict[str, Any]
    knowledge_snapshot_id: StrictStr

class DurablePrompt(BaseModel):
    model_config=ConfigDict(extra="forbid", strict=True)
    prompt_schema_version: StrictInt
    prompt_version: StrictInt
    idea_story_version: StrictInt
    platform: StrictStr
    outputs: list[PromptOutput] = Field(min_length=1)

def validate_draft(document: Mapping[str, Any], profile: PlatformProfile) -> PromptDraft:
    draft=PromptDraft.model_validate(dict(document))
    if draft.prompt_draft_schema_version != PROMPT_DRAFT_SCHEMA_VERSION:
        raise ValueError("unsupported prompt draft schema version")
    if any(not item.prompt.strip() for item in draft.outputs):
        raise ValueError("prompt text must not be blank")
    ratios=[item.aspect_ratio for item in draft.outputs]
    if len(ratios)!=len(set(ratios)) or set(ratios)!=set(profile.required_aspect_ratios):
        raise ValueError("prompt draft ratios do not match platform profile")
    return PromptDraft(prompt_draft_schema_version=1, outputs=sorted(draft.outputs,key=lambda x: profile.required_aspect_ratios.index(x.aspect_ratio)))

def durable_prompt(*, provider: str, model: str, platform: str, idea_story_version: int, snapshot_id: str, draft: PromptDraft) -> dict[str, Any]:
    return DurablePrompt(prompt_schema_version=1,prompt_version=1,idea_story_version=idea_story_version,platform=platform,outputs=[PromptOutput(provider=provider,model=model,aspect_ratio=o.aspect_ratio,prompt=o.prompt,generation_parameters={},knowledge_snapshot_id=snapshot_id) for o in draft.outputs]).model_dump(mode="json")

def assemble_prompt(*, target_provider: str, target_model: str, template_version: str, idea_story: Mapping[str, Any], knowledge_text: str, platform: PlatformProfile) -> str:
    context=json.dumps({"template_version":template_version,"target_provider":target_provider,"target_model":target_model,"platform":platform.platform,"required_aspect_ratios":list(platform.required_aspect_ratios),"idea_story":idea_story,"knowledge":knowledge_text},ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return "Write creative prompt text for the specified target only. Return strict prompt_draft_schema_version=1 with exactly one output per required aspect ratio. Do not invent provider parameters, model IDs, lineage, or metadata. Return creative text only.\n\nDETERMINISTIC CONTEXT:\n"+context

def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

def draft_schema() -> dict[str, Any]:
    return PromptDraft.model_json_schema()
