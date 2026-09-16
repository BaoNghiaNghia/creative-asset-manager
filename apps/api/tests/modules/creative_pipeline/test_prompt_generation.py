from app.modules.creative_pipeline.platforms import platform_profile
from app.modules.creative_pipeline.prompt_generation import (
    PromptDraft, validate_draft, assemble_prompt, durable_prompt,
    prompt_hash, PROMPT_DRAFT_SCHEMA_VERSION,
)
import pytest


def test_platform_profiles_define_required_ratios():
    assert platform_profile("etsy").required_aspect_ratios == ("1:1",)
    assert platform_profile("amazon").required_aspect_ratios == ("16:9", "9:16")


def test_prompt_draft_requires_exact_platform_ratios():
    profile = platform_profile("amazon")
    draft = {"prompt_draft_schema_version": PROMPT_DRAFT_SCHEMA_VERSION, "outputs": [
        {"aspect_ratio": "9:16", "prompt": "portrait"},
        {"aspect_ratio": "16:9", "prompt": "landscape"},
    ]}
    validated = validate_draft(draft, profile)
    assert [item.aspect_ratio for item in validated.outputs] == ["16:9", "9:16"]
    with pytest.raises(Exception):
        validate_draft({"prompt_draft_schema_version": 1, "outputs": [{"aspect_ratio": "16:9", "prompt": "x"}]}, profile)


def test_prompt_assembly_is_deterministic_and_durable():
    profile = platform_profile("etsy")
    context = {"title": "linen", "claims": ["natural"]}
    text_a = assemble_prompt(target_provider="seedance", target_model="seedance-1", template_version="seedance_prompt_v1", idea_story=context, knowledge_text="rules", platform=profile)
    text_b = assemble_prompt(target_provider="seedance", target_model="seedance-1", template_version="seedance_prompt_v1", idea_story=context, knowledge_text="rules", platform=profile)
    assert text_a == text_b
    assert prompt_hash(text_a) == prompt_hash(text_b)
    durable = durable_prompt(prompt_version=1, provider="seedance", model="seedance-1", platform="etsy", idea_story_version=1, snapshot_id="snap-1", draft=validate_draft({"prompt_draft_schema_version": 1, "outputs": [{"aspect_ratio": "1:1", "prompt": "x"}]}, profile))
    assert durable["prompt_schema_version"] == 1
    assert durable["outputs"][0]["knowledge_snapshot_id"] == "snap-1"
