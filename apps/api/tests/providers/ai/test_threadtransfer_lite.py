"""ThreadTransfer Lite contract: one Codex execution, two genuine native PNGs."""
from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from app.modules.image_generation.providers import PreparedImage, ReferenceImageInput
from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageProviderError, CodexImageRunnerConfig,
)

SKILL = "gatorhats-8869-slot1-threadtransfer-lite-v1"


def png(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (600, 600), color).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("mode,expected_error", [
    ("ok", None),
    ("missing_macro", "slot1_threadtransfer_missing_pass"),
    ("wrong_final", "slot1_threadtransfer_native_unverified"),
    ("extra_generation", "slot1_threadtransfer_native_unverified"),
    ("one_generation", "slot1_threadtransfer_native_unverified"),
])
def test_lite_native_two_image_contract(tmp_path, monkeypatch, mode, expected_error):
    home = tmp_path / "home"
    skill = home / "skills" / SKILL
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {SKILL}\n---\n", encoding="utf-8")
    macro_bytes = png("blue")
    final_bytes = png("green")
    cache_files = [macro_bytes, final_bytes]
    if mode == "one_generation":
        cache_files = cache_files[:1]
    if mode == "extra_generation":
        cache_files.append(png("yellow"))
    captured = {}

    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b'{"type":"thread.started","thread_id":"test-two-pass"}\n')
            self.stdout.feed_eof()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()
            workspace = Path(captured["workspace"])
            native = workspace / ".codex-runtime" / "generated_images" / str(UUID(int=11))
            native.mkdir(parents=True)
            for index, data in enumerate(cache_files):
                (native / f"exec-{UUID(int=index + 1)}.png").write_bytes(data)
            working = workspace / "working"
            working.mkdir()
            if mode != "missing_macro":
                (working / "thread_macro.png").write_bytes(macro_bytes)
            (workspace / "output" / "final.png").write_bytes(
                png("red") if mode == "wrong_final" else final_bytes
            )

        async def wait(self):
            return 0

    async def fake_process(*args, **kwargs):
        captured["args"] = args
        captured["workspace"] = args[args.index("-C") + 1]
        return FakeProcess()

    monkeypatch.setattr("app.providers.ai.codex_image.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr("app.providers.ai.codex_image.asyncio.create_subprocess_exec", fake_process)
    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        codex_home=str(home), staging_root=str(tmp_path / "staging"),
        skill_name=SKILL, output_contract="slot1_two_pass", timeout_seconds=30,
    ))
    inputs = [
        ReferenceImageInput(image=PreparedImage(
            image_bytes=png("tan"), mime_type="image/png", width=600, height=600
        ), role=role, label=role) for role in (
            "design_reference", "unaltered_stock_reference", "slot1_edit_mask",
        )
    ]
    coro = runner.generate_from_references(
        attempt_id="only-test", person=PreparedImage(
            image_bytes=png("white"), mime_type="image/png", width=600, height=600
        ), references=inputs, prompt="Execute Lite.",
    )
    if expected_error:
        with pytest.raises(CodexImageProviderError) as caught:
            asyncio.run(coro)
        assert caught.value.code == expected_error
    else:
        result = asyncio.run(coro)
        assert result.image_bytes == final_bytes
        assert result.provider_metadata["pipeline_version"] == "threadtransfer-lite-v1"
        assert result.provider_metadata["native_passes"] == 2
        assert result.provider_metadata["macro_sha256"] != result.provider_metadata["edited_sha256"]
    argv = captured["args"]
    assert argv.count("exec") == 1
    assert "model_reasoning_effort=low" in argv
    assert "thread_macro.png" in argv[-1]
    assert "slot1_edit_mask" in argv[-1]
    assert not (Path(captured["workspace"]) / ".codex-runtime" / "generated_images").exists()


def test_lite_prompt_does_not_stack_legacy_stage2_instructions():
    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        skill_name=SKILL, output_contract="slot1_two_pass"
    ))
    prompt = runner._prompt(person_name="person.png",
        references=[("design_reference", "design.png"), ("slot1_edit_mask", "mask.png")],
        user_prompt="Keep exact design.")
    assert "Use $" + SKILL in prompt
    assert "exactly two" in prompt.lower()
    assert "working/thread_macro.png" in prompt
    assert "mask.png" in prompt
    assert len(prompt) < 1000


def test_lite_manifest_matches_slot1():
    path = Path(__file__).resolve().parents[5] / "deploy" / "codex" / "skills" / SKILL
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skill_name"] == SKILL
    assert manifest["required_reference_roles"] == [
        "design_reference", "unaltered_stock_reference", "slot1_edit_mask",
    ]
    text = (path / "SKILL.md").read_text(encoding="utf-8")
    assert "working/thread_macro.png" in text
    assert "output/final.png" in text
