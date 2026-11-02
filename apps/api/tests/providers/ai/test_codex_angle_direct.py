"""Native Codex direct stock edit is a built-in one-pass contract, not a Skill."""
from pathlib import Path

import pytest

from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageRunnerConfig, SLOT1_DIRECT_NAME,
)
from app.modules.realistic_review_ugc.angle_direct_edit import angle_direct_prompt


def runner():
    return CodexImageGenRunner(CodexImageRunnerConfig(
        binary="codex", codex_home="/tmp/nonexistent-codex",
        skill_name=SLOT1_DIRECT_NAME,
        output_contract="stage2_angle_direct_single",
    ))


@pytest.mark.parametrize("stage",["prod_s2_ec2","prod_s2_ec3"])
def test_angle_prompt_requires_single_native_edit_and_exact_reference_roles(stage):
    prompt=runner()._prompt(
        person_name="person.jpg",
        references=[
            ("slot1_thread_material_reference","verified-macro.png"),
            ("design_reference","original.png"),
        ],
        user_prompt=angle_direct_prompt(stage),
    )
    assert "ONE" in prompt and "IMAGE-EDIT MODE" in prompt
    assert "original official" in prompt.lower() or "original" in prompt.lower()
    assert "Do NOT produce a square guide, edit mask" in prompt
    assert "ONE IMAGE-EDIT invocation only" in prompt
    assert "native ImageGen edited PNG" in prompt
    assert "No mask image" in prompt
    assert "stock" in prompt.lower()
    assert "GatorHats" in prompt
    assert "$" + SLOT1_DIRECT_NAME not in prompt


def test_direct_angle_is_built_in_not_an_uploaded_skill(monkeypatch):
    monkeypatch.setattr("app.providers.ai.codex_image.shutil.which",
                        lambda _binary:"/usr/local/bin/codex")
    assert runner().capability_reason() is None


@pytest.mark.parametrize("roles",[
    [],
    [("design_reference","design.png")],
    [("design_reference","design.png"),("slot1_thread_material_reference","macro.png")],
    [("slot1_thread_material_reference","macro.png"),("design_reference","design.png"),
     ("slot1_edit_mask","mask.png")],
])
def test_angle_rejects_unpinned_or_unordered_reference_inputs(roles):
    with pytest.raises(ValueError,match="only pinned material B and design C"):
        runner()._prompt(person_name="person.jpg",references=roles,
                         user_prompt=angle_direct_prompt("prod_s2_ec2"))
