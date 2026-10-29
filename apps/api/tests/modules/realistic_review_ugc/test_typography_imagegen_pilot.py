"""G6 one-slot native ImageGen provider and immutable review-staging tests."""
from __future__ import annotations

import asyncio
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

from app.modules.image_generation.providers import GeneratedImageResult
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, enabled, fingerprint, generate_one_slot, PILOT_SCHEMA,
    fit_native_on_stage1_canvas,
)
from app.modules.realistic_review_ugc import typography_imagegen_once as cli
from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError,
    load_codex_skill_manifest,
)
from app.providers.ai import codex_image


def valid_png(color=(26, 70, 130, 255), *, opaque=False):
    image = Image.new("RGBA", (2048, 1024), (0, 0, 0, 255 if opaque else 0))
    ImageDraw.Draw(image).rectangle((390, 280, 1680, 735), fill=color)
    buf = BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def settings(tmp_path, **changes):
    values = dict(
        TYPOGRAPHY_V2_ENABLED=True,
        TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=True,
        TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=True,
        TYPOGRAPHY_V2_CANARY_TENANT_IDS="tenant-a",
        IMAGE_GENERATION_ENABLED=True, CODEX_IMAGE_GENERATION_ENABLED=True,
        IMAGE_GENERATION_STAGING_ROOT=str(tmp_path),
    )
    values.update(changes)
    return SimpleNamespace(**values)


def plan():
    return {
        "plan_version": "concept-short-v2/plan-v1", "skill": "concept-short-v2",
        "source_keyword_exact": "SÀI GÒN & Cà phê",
        "preview_qa": {"all_technical_pass": True},
        "job_created": False, "ready_for_imagegen": False,
        "slots": [
            {"slot": i, "status": "pending",
             "source_keyword_exact": "SÀI GÒN & Cà phê",
             "prompt": f"Approved slot {i} for literal SÀI GÒN & Cà phê"}
            for i in range(1, 7)
        ],
    }


class FakeRunner:
    def __init__(self, data, *, native=True):
        self.config = SimpleNamespace(skill_name="concept-short-v2",
                                      output_contract="typography_v2_single_slot")
        self.data = data
        self.native = native
        self.calls = []
        self.cleaned = []

    async def generate_from_references(self, **kwargs):
        self.calls.append(kwargs)
        return GeneratedImageResult(
            provider="codex", model="imagegen", image_bytes=self.data,
            mime_type="image/png",
            provider_metadata={
                "typography_v2_native_verified": self.native,
                "native_sha256": sha256(self.data).hexdigest() if self.native else "unverified",
            },
        )

    def cleanup_attempt(self, attempt_id):
        self.cleaned.append(attempt_id)


def execute(tmp_path, *, runner=None, spec=None, slot=1, batch=None, revalidate=None, opts=None):
    plan_data = spec or plan()
    provider = runner or FakeRunner(valid_png())
    return asyncio.run(generate_one_slot(
        settings=opts or settings(tmp_path), tenant_id="tenant-a",
        batch_id=batch or str(uuid4()), slot=slot, plan=plan_data,
        runner=provider, revalidate=revalidate or (lambda: plan_data),
    )), provider


@pytest.mark.parametrize("overrides", [
    {"TYPOGRAPHY_V2_ENABLED": False},
    {"TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED": False},
    {"TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED": False},
    {"CODEX_IMAGE_GENERATION_ENABLED": False},
    {"IMAGE_GENERATION_ENABLED": False},
    {"TYPOGRAPHY_V2_CANARY_TENANT_IDS": ""},
])
def test_pilot_guards_fail_closed_before_provider_or_storage(tmp_path, overrides):
    runner = FakeRunner(valid_png())
    with pytest.raises(TypographyPilotError, match="disabled"):
        execute(tmp_path, runner=runner, opts=settings(tmp_path, **overrides))
    assert runner.calls == []
    assert not (tmp_path / "typography-v2-pilot").exists()


def test_one_real_verified_png_is_staged_for_manual_review_only(tmp_path):
    runner = FakeRunner(valid_png())
    result, runner = execute(tmp_path, runner=runner)
    assert result["status"] == "needs_human_review"
    assert result["published"] is False
    assert len(runner.calls) == 1 and len(runner.cleaned) == 1
    assert runner.calls[0]["references"] == []
    assert runner.calls[0]["prompt"] == plan()["slots"][0]["prompt"]
    data = Path(result["image_path"]).read_bytes()
    assert data == runner.data
    record = json.loads(Path(result["audit_path"]).read_text())
    assert record["schema_version"] == PILOT_SCHEMA
    assert record["native_imagegen_verified"] is True
    assert record["technical_pass"] is True
    assert record["review"] == "pending"
    assert record["image_spelling_verified"] is False
    assert record["stage1_output_saved"] is False
    assert record["eligible_for_publication"] is False
    assert record["sha256"] == sha256(data).hexdigest()
    assert record["plan_sha256"] == fingerprint(plan())
    assert "SÀI GÒN" not in Path(result["audit_path"]).read_text()


def test_smaller_native_png_gets_transparent_padding_without_resampling(tmp_path):
    image = Image.new("RGBA", (1536, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((200, 260, 1330, 720), fill=(66, 88, 145, 255))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    native = buffer.getvalue()
    padded, changed = fit_native_on_stage1_canvas(native)
    assert changed is True
    with Image.open(BytesIO(padded)) as output:
        assert output.size == (2048, 1024)
        # Centered exact source pixels, no interpolation, no invented stitches.
        assert output.getpixel((1024, 512)) == (66, 88, 145, 255)
        assert output.getpixel((0, 0)) == (0, 0, 0, 0)
    native_reused, changed = fit_native_on_stage1_canvas(valid_png())
    assert changed is False and native_reused == valid_png()
    result, provider = execute(tmp_path, runner=FakeRunner(native))
    assert result["published"] is False
    record = json.loads(Path(result["audit_path"]).read_text())
    assert record["native_sha256"] == sha256(native).hexdigest()
    assert record["transparent_padding_only"] is True
    assert record["sha256"] == sha256(Path(result["image_path"]).read_bytes()).hexdigest()
    assert record["technical_pass"] is True


def test_wrong_provider_or_plan_fails_before_generation(tmp_path):
    wrong = FakeRunner(valid_png())
    wrong.config.skill_name = "concept-short-v1"
    with pytest.raises(TypographyPilotError, match="Wrong"):
        execute(tmp_path, runner=wrong)
    assert wrong.calls == []
    bad_plan = plan()
    bad_plan["slots"][1]["slot"] = 1
    with pytest.raises(TypographyPilotError, match="six"):
        execute(tmp_path, spec=bad_plan)


@pytest.mark.parametrize("blob,native,error", [
    (valid_png(), False, "native_image_unverified"),
    (valid_png(opaque=True), True, "native_alpha_invalid"),
    (b"fake_png", True, "native_png_invalid"),
])
def test_invalid_or_fabricated_output_never_persists(tmp_path, blob, native, error):
    batch = str(uuid4())
    runner = FakeRunner(blob, native=native)
    with pytest.raises(TypographyPilotError) as exc:
        execute(tmp_path, batch=batch, runner=runner)
    assert exc.value.code == error
    assert len(runner.cleaned) == 1
    assert not list((tmp_path / "typography-v2-pilot").rglob("*.png"))
    assert not list((tmp_path / "typography-v2-pilot").rglob("slot_*.json"))


def test_reference_revoked_during_generation_rejects_before_persist(tmp_path):
    runner = FakeRunner(valid_png())
    batch = str(uuid4())

    def new_plan():
        revised = plan()
        revised["source_keyword_exact"] = "Changed or rights revoked"
        return revised

    with pytest.raises(TypographyPilotError) as exc:
        execute(tmp_path, runner=runner, batch=batch, revalidate=new_plan)
    assert exc.value.code == "source_changed"
    assert runner.cleaned
    assert not list((tmp_path / "typography-v2-pilot").rglob("*.png"))


def test_repeating_slot_never_reruns_provider_or_overwrites(tmp_path):
    batch = str(uuid4())
    first, _ = execute(tmp_path, batch=batch)
    original = Path(first["image_path"]).read_bytes()
    runner = FakeRunner(valid_png(color=(123, 40, 45, 255)))
    with pytest.raises(TypographyPilotError) as exc:
        execute(tmp_path, batch=batch, runner=runner)
    assert exc.value.code == "slot_exists"
    assert runner.calls == []
    assert Path(first["image_path"]).read_bytes() == original


def test_global_single_flight_blocks_separate_batches_without_provider_calls(tmp_path):
    import fcntl
    execute(tmp_path)
    lock_path = tmp_path / "typography-v2-pilot" / ".imagegen-single-flight.lock"
    assert lock_path.exists()
    runner = FakeRunner(valid_png(color=(185, 25, 20, 255)))
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TypographyPilotError) as exc:
            execute(tmp_path, runner=runner, batch=str(uuid4()))
        assert exc.value.code == "imagegen_busy"
        assert runner.calls == []
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def test_duplicate_generated_image_cannot_fill_two_slots(tmp_path):
    batch = str(uuid4())
    first, _ = execute(tmp_path, batch=batch)
    with pytest.raises(TypographyPilotError) as exc:
        execute(tmp_path, batch=batch, slot=2)
    assert exc.value.code == "duplicate_slot_image"
    assert Path(first["image_path"]).is_file()
    assert len(list((tmp_path / "typography-v2-pilot").rglob("design_*.png"))) == 1


def test_two_unique_pilot_slots_remain_pending_human_review(tmp_path):
    batch = str(uuid4())
    first, _ = execute(tmp_path, batch=batch)
    second, _ = execute(tmp_path, batch=batch, slot=2,
                        runner=FakeRunner(valid_png(color=(180, 35, 60, 255))))
    assert Path(first["image_path"]).is_file()
    assert Path(second["image_path"]).is_file()
    for record_path in (first["audit_path"], second["audit_path"]):
        assert json.loads(Path(record_path).read_text())["review"] == "pending"


def test_batch_uuid_blocks_traversal_and_cannot_call_provider(tmp_path):
    runner = FakeRunner(valid_png())
    with pytest.raises(TypographyPilotError) as exc:
        execute(tmp_path, runner=runner, batch="../wrong")
    assert exc.value.code == "batch_id_invalid"
    assert not runner.calls


def test_cli_dry_run_and_disabled_are_side_effect_free(monkeypatch, capsys):
    def never_settings():
        raise AssertionError("Dry run must not even load production Settings")
    monkeypatch.setattr(cli, "get_settings", never_settings)
    args = ["--tenant-id", "t", "--user-id", "u", "--keyword-id", "k",
            "--reference-id", "r", "--batch-id", str(uuid4()), "--slot", "1"]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run_only"
    monkeypatch.setattr(cli, "get_settings", lambda: settings(
        Path("/tmp/typography-pilot-tests-not-used"), TYPOGRAPHY_V2_ENABLED=False))
    assert cli.main(args + ["--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pilot_disabled"


def test_skill_manifest_is_pilot_only_and_not_stage1_v1_selectable(tmp_path):
    repo = Path(__file__).resolve().parents[3] / "managed_skills" / "concept-short-v2"
    home = tmp_path / "codex_home" / "skills" / "concept-short-v2"
    home.mkdir(parents=True)
    for filename in ("SKILL.md", "manifest.json"):
        (home / filename).write_bytes((repo / filename).read_bytes())
    parsed = load_codex_skill_manifest(str(tmp_path / "codex_home"), "concept-short-v2")
    assert parsed is not None
    assert parsed.workflows == ("typography_v2_single_slot",)
    assert "keyword_six_designs" not in parsed.workflows
    from app.modules.realistic_review_ugc.stage_skill_settings import keyword_skill_compatible
    assert keyword_skill_compatible(
        "concept-short-v2",
        settings=SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path / "codex_home")),
    ) is False


@pytest.mark.parametrize("scenario", ["native", "no_native", "forged_final"])
def test_codex_native_cache_byte_provenance_is_mandatory(tmp_path, monkeypatch, scenario):
    home = tmp_path / "home"
    skill_dir = home / "skills" / "concept-short-v2"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("name: concept-short-v2")
    cache_uuid = "12345678-1234-4567-8abc-123456789abc"
    image_uuid = "abcdefab-1111-4222-8ccc-123456789abc"
    native = valid_png()
    forged = valid_png(color=(190, 20, 50, 255))

    async def fake_subprocess(*args, **kwargs):
        workspace = Path(args[args.index("-C") + 1])
        final = workspace / "output" / "final.png"
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(forged if scenario == "forged_final" else native)
        if scenario != "no_native":
            images = Path(kwargs["env"]["CODEX_HOME"]) / "generated_images" / cache_uuid
            images.mkdir(parents=True)
            (images / ("exec-" + image_uuid + ".png")).write_bytes(native)
        return SimpleNamespace(returncode=0)

    async def fake_stream(*_args, **_kwargs):
        return json.dumps({"type": "turn.completed"}), ""

    monkeypatch.setattr(codex_image.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(codex_image.asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(codex_image, "stream_codex_process", fake_stream)
    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        codex_home=str(home), staging_root=str(tmp_path / "stage"),
        skill_name="concept-short-v2", output_contract="typography_v2_single_slot",
        timeout_seconds=60,
    ))
    try:
        if scenario == "native":
            result = asyncio.run(runner.generate_from_references(
                attempt_id="job-native", person=None, references=[], prompt="Plan one slot",
            ))
            assert result.image_bytes == native
            assert result.provider_metadata["typography_v2_native_verified"] is True
            assert result.provider_metadata["native_sha256"] == sha256(native).hexdigest()
        else:
            with pytest.raises(CodexImageProviderError) as exc:
                asyncio.run(runner.generate_from_references(
                    attempt_id="job-native", person=None, references=[], prompt="Plan one slot",
                ))
            assert exc.value.code == "typography_v2_native_provenance_missing"
        assert not (tmp_path / "stage" / "codex" / "job-native" / ".codex-runtime" / "generated_images").exists()
    finally:
        runner.cleanup_attempt("job-native")
