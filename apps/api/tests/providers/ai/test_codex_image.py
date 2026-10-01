from __future__ import annotations

import asyncio
import os
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.modules.image_generation.providers import PreparedImage, ReferenceImageInput
from app.providers.ai.codex_image import (
    CodexImageGenRunner,
    CodexImageRunnerConfig,
    _classify_failure,
)


def _png(value: int = 120) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), (value, value, value)).save(output, "PNG")
    return output.getvalue()


def _prepared(value: int = 120) -> PreparedImage:
    return PreparedImage(
        image_bytes=_png(value),
        mime_type="image/png",
        width=32,
        height=32,
    )


def test_codex_runner_materializes_role_files_and_scrubs_api_credentials(
    tmp_path,
    monkeypatch,
):
    codex_home = tmp_path / "codex-home"
    skill_dir = codex_home / "skills" / "worker-hat-v1"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: worker-hat-v1\ndescription: test\n---\nGenerate one image.\n",
        encoding="utf-8",
    )
    staging_root = tmp_path / "staging"
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            workspace = Path(str(captured["workspace"]))
            output = workspace / "output" / "final.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(_png(210))
            return (
                b'{"type":"thread.started","thread_id":"thread-test-1"}\n',
                b"",
            )

        def kill(self):
            self.returncode = -9

    async def fake_create_subprocess_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        captured["stdin"] = kwargs["stdin"]
        cd_index = argv.index("-C")
        captured["workspace"] = argv[cd_index + 1]
        return FakeProcess()

    monkeypatch.setattr(
        "app.providers.ai.codex_image.shutil.which",
        lambda _binary: "/usr/local/bin/codex",
    )
    monkeypatch.setattr(
        "app.providers.ai.codex_image.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("OPENAI_PROJECT", "must-not-leak")

    runner = CodexImageGenRunner(
        CodexImageRunnerConfig(
            codex_home=str(codex_home),
            staging_root=str(staging_root),
            skill_name="worker-hat-v1",
            timeout_seconds=30,
        )
    )
    result = asyncio.run(
        runner.generate_from_references(
            attempt_id="attempt-1",
            person=_prepared(100),
            references=[
                ReferenceImageInput(
                    image=_prepared(150),
                    role="product",
                    label="front",
                )
            ],
            prompt="Preserve identity.",
        )
    )

    assert result.provider == "codex"
    assert result.provider_request_id == "thread-test-1"
    assert result.mime_type == "image/png"
    assert result.image_bytes == _png(210)
    argv = tuple(captured["argv"])
    assert "exec" in argv
    assert "--ephemeral" in argv
    assert "--sandbox" not in argv
    assert "--approve-for-me" in argv
    assert "Use $worker-hat-v1 and $imagegen." in argv[-1]
    assert "product-front.png" in argv[-1]
    assert captured["stdin"] == asyncio.subprocess.DEVNULL
    env = dict(captured["env"])
    assert env["CODEX_HOME"] == str(codex_home)
    assert "OPENAI_API_KEY" not in env
    assert "OPENAI_PROJECT" not in env
    workspace = Path(str(captured["workspace"]))
    assert (workspace / "person.png").is_file()
    assert (workspace / "product-front.png").is_file()


def test_codex_runner_classifies_usage_limit_as_deferred():
    error = _classify_failure(
        "You have reached your usage limit. Try again later.",
        "",
    )
    assert error.code == "codex_image_usage_limited"
    assert error.defer_seconds == 3600
    assert error.retryable is False


def test_codex_runner_requires_installed_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.providers.ai.codex_image.shutil.which",
        lambda _binary: "/usr/local/bin/codex",
    )
    runner = CodexImageGenRunner(
        CodexImageRunnerConfig(
            codex_home=str(tmp_path / "codex-home"),
            staging_root=str(tmp_path / "staging"),
            skill_name="worker-hat-v1",
        )
    )
    assert runner.capability_reason() == "Codex skill $worker-hat-v1 is not installed."
