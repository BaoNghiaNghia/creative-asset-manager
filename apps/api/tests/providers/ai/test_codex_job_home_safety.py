from __future__ import annotations

from pathlib import Path
from io import BytesIO
from PIL import Image
from app.providers.ai.codex_image import (CodexImageGenRunner,CodexImageRunnerConfig,CodexImageProviderError,_isolated_job_codex_home)
import pytest

def _png():
    buf=BytesIO()
    Image.new("RGB",(32,32),"red").save(buf,"PNG")
    return buf.getvalue()

def test_codex_job_home_isolated_cache_and_cleanup_preserves_final_checkpoint(tmp_path):
    import pytest
    from app.providers.ai.codex_image import CodexImageProviderError

    source = tmp_path / "account"
    source.mkdir()
    (source / "auth.json").write_text('{"auth":"placeholder"}', encoding="utf-8")
    (source / "config.toml").write_text("model = 'gpt'\n", encoding="utf-8")
    (source / "skills").mkdir()
    root = tmp_path / "stage"
    workspace = root / "codex" / "job-one"
    finals = workspace / "output" / "final"
    finals.mkdir(parents=True)
    (finals / "design_01.png").write_bytes(_png())
    runtime = _isolated_job_codex_home(source, workspace)
    assert runtime != source
    assert (runtime / "auth.json").is_symlink()
    assert (runtime / "skills").is_symlink()
    assert (runtime / "generated_images").is_dir()
    assert not (source / "generated_images").exists()
    cache_png = runtime / "generated_images" / "draft.png"
    cache_png.write_bytes(_png())
    previews = workspace / "working" / "previews"
    previews.mkdir(parents=True)
    (previews / "draft.png").write_bytes(_png())

    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        codex_home=str(source), staging_root=str(root),
    ))
    runner.cleanup_previews("job-one")
    assert not cache_png.exists()
    assert not previews.exists()
    assert (finals / "design_01.png").is_file()
    assert (source / "auth.json").is_file()
    assert (source / "skills").is_dir()
    assert (runtime / "auth.json").is_symlink()
    runner.cleanup_attempt("job-one")
    assert not workspace.exists()
    assert (source / "auth.json").exists()

    other = root / "codex" / "job-two"
    other.mkdir()
    (other / ".codex-runtime").symlink_to(source, target_is_directory=True)
    with pytest.raises(CodexImageProviderError, match="unsafe"):
        _isolated_job_codex_home(source, other)

def test_timeout_drops_job_cache_but_keeps_partial_final(tmp_path, monkeypatch):
    import asyncio
    account=tmp_path/"account"
    skill=account/"skills"/"worker-hat-v1"
    skill.mkdir(parents=True)
    (skill/"SKILL.md").write_text("test",encoding="utf-8")
    staging=tmp_path/"staging"
    captured={}
    class FakeProcess:
        returncode=None
        def kill(self):
            self.returncode=-9
        async def wait(self):
            return self.returncode

    async def fake_start(*args,**kwargs):
        captured["home"]=Path(kwargs["env"]["CODEX_HOME"])
        captured["home"].joinpath("generated_images","preview.png").write_bytes(_png())
        root=Path(args[args.index("-C")+1])
        final=root/"output"/"final"/"design_01.png"
        final.parent.mkdir(parents=True,exist_ok=True)
        final.write_bytes(_png())
        return FakeProcess()

    async def fake_stream(*args,**kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr("app.providers.ai.codex_image.shutil.which",lambda name:"/usr/bin/codex")
    monkeypatch.setattr("app.providers.ai.codex_image.asyncio.create_subprocess_exec",fake_start)
    monkeypatch.setattr("app.providers.ai.codex_image.stream_codex_process",fake_stream)
    runner=CodexImageGenRunner(CodexImageRunnerConfig(
        codex_home=str(account),staging_root=str(staging),skill_name="worker-hat-v1",
        output_contract="stage1_six_final_designs",timeout_seconds=1,
    ))
    with pytest.raises(CodexImageProviderError) as err:
        asyncio.run(runner.generate_from_references(
            attempt_id="retry-job",person=None,references=[],prompt="Generate final"
        ))
    assert err.value.code=="codex_image_timeout"
    assert not (captured["home"]/"generated_images").exists()
    assert (staging/"codex"/"retry-job"/"output"/"final"/"design_01.png").is_file()
    assert not (account/"generated_images").exists()
