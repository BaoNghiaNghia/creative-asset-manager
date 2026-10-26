"""Regression for opt-in Concept Short image edits using the real source plan."""
import asyncio
import base64
import hashlib
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.providers.ai import codex_image
from app.providers.ai.cloudflare_flux_klein import (
    CloudflareFluxKleinImageEditProvider, CloudflareFluxEditError,
)
from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError,
)


def image_data(size=(1536, 1024), color=(35, 68, 170)):
    image = Image.new("RGB", size, color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_cloudflare_reference_edit_uses_multipart_and_small_source():
    requests = []

    async def handler(request):
        body = request.content
        assert b'name="input_image_0"' in body
        assert b'name="prompt"' in body
        assert b"Exact concept prompt" in body
        assert b'name="width"' in body
        assert b"1536" in body
        assert b"504" not in request.headers.get("content-type", "").encode()
        requests.append(request)
        return httpx.Response(200, json={"success": True, "result": {
            "image": base64.b64encode(image_data()).decode("ascii"),
        }})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CloudflareFluxKleinImageEditProvider(
        account_id="test-account", api_token="test-token", http_client=client,
    )
    try:
        result = asyncio.run(adapter.edit_reference(
            image_bytes=image_data(), prompt="Exact concept prompt",
        ))
        with Image.open(BytesIO(result)) as img:
            assert img.size == (1536, 1024)
        assert len(requests) == 1
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize("http_code,expected_code,retryable", [
    (429, "concept_short_flux_rate_limited", True),
    (401, "concept_short_flux_auth_failed", False),
    (500, "concept_short_flux_unavailable", True),
])
def test_cloudflare_errors_fail_closed_no_synthetic_fallback(http_code, expected_code, retryable):
    async def handler(_request):
        return httpx.Response(http_code)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CloudflareFluxKleinImageEditProvider(
        account_id="test", api_token="test", http_client=client,
    )
    try:
        with pytest.raises(CloudflareFluxEditError) as caught:
            asyncio.run(adapter.edit_reference(image_bytes=image_data(), prompt="text"))
        assert caught.value.code == expected_code
        assert caught.value.retryable is retryable
    finally:
        asyncio.run(client.aclose())


def test_cloudflare_invalid_source_fails_without_request():
    adapter = CloudflareFluxKleinImageEditProvider(account_id="test", api_token="test")
    try:
        with pytest.raises(CloudflareFluxEditError) as caught:
            asyncio.run(adapter.edit_reference(image_bytes=b"bad", prompt="text"))
        assert caught.value.code == "concept_short_flux_source_invalid"
    finally:
        asyncio.run(adapter.aclose())


def make_source(workspace):
    folder = workspace / "working" / "concept-short" / "cowboys-and-tequila"
    folder.mkdir(parents=True)
    names = ("C1", "C2", "C3", "C7", "C5", "C6")
    (folder / "summary.json").write_text(json.dumps([
        {"concept": name, "status": "ok"} for name in names
    ]))
    for n, name in enumerate(names, 1):
        source = folder / name
        source.mkdir()
        (source / "A_input.png").write_bytes(image_data(color=(20+n, 66+n, 170+n)))
        (source / "A_prompt.txt").write_text("Stitch edit slot " + name)
        (source / "spec.json").write_text("{}")
    return folder


class FakeFlux:
    seen = []
    fail_at = None
    failure_code = "concept_short_flux_rate_limited"

    def __init__(self, *, account_id, api_token):
        assert account_id == "acct"
        assert api_token == "token"

    async def edit_reference(self, *, image_bytes, prompt, width=1536, height=1024):
        self.seen.append(prompt)
        number = len(self.seen)
        if number == self.fail_at:
            raise CloudflareFluxEditError(self.failure_code, "failed edit", retryable=self.failure_code == "concept_short_flux_rate_limited")
        concept = prompt.rsplit(" ", 1)[-1]
        color_index = {"C1": 1, "C2": 2, "C3": 3, "C7": 4, "C5": 5, "C6": 6}[concept]
        return image_data(color=(color_index*29, 20, 180))

    async def aclose(self):
        pass


def runner(tmp_path):
    home = tmp_path / "home"
    skill = home / "skills" / "concept-short-v1"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("name: concept-short-v1")
    return CodexImageGenRunner(CodexImageRunnerConfig(
        binary="missing-codex-binary",
        codex_home=str(home),
        staging_root=str(tmp_path / "staging"),
        skill_name="concept-short-v1",
        output_contract="stage1_six_final_designs",
        expected_quote="Cowboys AND Tequila",
        concept_short_cloudflare_enabled=True,
        concept_short_cloudflare_account_id="acct",
        concept_short_cloudflare_api_token="token",
    ))


def test_concept_short_six_direct_edits_without_codex_cli(tmp_path, monkeypatch):
    async def build(*, script, quote, workspace):
        assert quote == "Cowboys AND Tequila"
        make_source(workspace)

    FakeFlux.seen = []
    FakeFlux.fail_at = None
    monkeypatch.setattr(codex_image, "_build_concept_short_source", build)
    from app.providers.ai import cloudflare_flux_klein
    monkeypatch.setattr(cloudflare_flux_klein, "CloudflareFluxKleinImageEditProvider", FakeFlux)
    monkeypatch.setattr(codex_image.shutil, "which", lambda _: None)
    actual = runner(tmp_path)
    generated = asyncio.run(actual.generate_from_references(
        attempt_id="new-job", person=None, references=[], prompt="Cowboys AND Tequila",
    ))
    assert len(generated.output_files) == 6
    assert FakeFlux.seen == [
        "Stitch edit slot C1", "Stitch edit slot C2", "Stitch edit slot C3",
        "Stitch edit slot C7", "Stitch edit slot C5", "Stitch edit slot C6",
    ]
    assert generated.model == "@cf/black-forest-labs/flux-2-klein-4b"


def test_concept_short_can_resume_only_missing_edits(tmp_path, monkeypatch):
    from app.providers.ai import cloudflare_flux_klein
    FakeFlux.seen = []
    FakeFlux.fail_at = 3

    async def build(*, script, quote, workspace):
        make_source(workspace)
    monkeypatch.setattr(codex_image, "_build_concept_short_source", build)
    monkeypatch.setattr(cloudflare_flux_klein, "CloudflareFluxKleinImageEditProvider", FakeFlux)
    monkeypatch.setattr(codex_image.shutil, "which", lambda _: None)
    actual = runner(tmp_path)
    partial = asyncio.run(actual.generate_from_references(
        attempt_id="partial-job", person=None, references=[], prompt="Cowboys AND Tequila",
    ))
    assert len(partial.output_files) == 2
    assert partial.provider_metadata["saved_output_count"] == "2"
    workspace = tmp_path / "staging" / "codex" / "partial-job"
    final = workspace / "output" / "final"
    originals = {n: hashlib.sha256((final / f"design_{n:02}.png").read_bytes()).hexdigest() for n in (1, 2)}
    assert actual.stage1_missing_final_numbers(workspace, 6) == (3, 4, 5, 6)

    FakeFlux.seen = []
    FakeFlux.fail_at = None
    generated = asyncio.run(actual.generate_from_references(
        attempt_id="partial-job", person=None, references=[], prompt="Cowboys AND Tequila",
        resume_missing_numbers=(3, 4, 5, 6),
    ))
    assert len(generated.output_files) == 6
    assert FakeFlux.seen == [
        "Stitch edit slot C3", "Stitch edit slot C7",
        "Stitch edit slot C5", "Stitch edit slot C6",
    ]
    assert all(hashlib.sha256((final / f"design_{n:02}.png").read_bytes()).hexdigest() == originals[n] for n in originals)




def test_concept_short_provider_rejection_is_skipped_but_other_slots_survive(tmp_path, monkeypatch):
    from app.providers.ai import cloudflare_flux_klein
    async def build(*, script, quote, workspace):
        make_source(workspace)
    FakeFlux.seen = []
    FakeFlux.fail_at = 5
    FakeFlux.failure_code = "concept_short_flux_rejected"
    monkeypatch.setattr(codex_image, "_build_concept_short_source", build)
    monkeypatch.setattr(cloudflare_flux_klein, "CloudflareFluxKleinImageEditProvider", FakeFlux)
    monkeypatch.setattr(codex_image.shutil, "which", lambda _: None)
    result = asyncio.run(runner(tmp_path).generate_from_references(
        attempt_id="reject-slot-5", person=None, references=[], prompt="Cowboys AND Tequila",
    ))
    FakeFlux.failure_code = "concept_short_flux_rate_limited"
    assert len(result.output_files) == 5
    assert {item.filename.rsplit("/", 1)[-1] for item in result.output_files} == {
        "design_01.png", "design_02.png", "design_03.png",
        "design_04.png", "design_06.png",
    }
    assert result.provider_metadata["skipped_slot_numbers"] == "5"
    assert len(FakeFlux.seen) == 6


def test_concept_short_source_with_ineligible_slot_keeps_other_designs(tmp_path, monkeypatch):
    from app.providers.ai import cloudflare_flux_klein
    async def build(*, script, quote, workspace):
        folder = make_source(workspace)
        summary = json.loads((folder / "summary.json").read_text())
        summary[-1]["status"] = "skipped"
        summary[-1]["reason"] = "C6 max 10 chars per line"
        (folder / "summary.json").write_text(json.dumps(summary))
    FakeFlux.seen = []
    FakeFlux.fail_at = None
    monkeypatch.setattr(codex_image, "_build_concept_short_source", build)
    monkeypatch.setattr(cloudflare_flux_klein, "CloudflareFluxKleinImageEditProvider", FakeFlux)
    monkeypatch.setattr(codex_image.shutil, "which", lambda _: None)
    result = asyncio.run(runner(tmp_path).generate_from_references(
        attempt_id="skipped-slot-6", person=None, references=[], prompt="Cowboys AND Tequila",
    ))
    assert len(result.output_files) == 5
    assert result.provider_metadata["skipped_slot_numbers"] == "6"
    assert not any("C6" in x for x in FakeFlux.seen)


def test_concept_short_zero_successful_edits_remains_failed(tmp_path, monkeypatch):
    from app.providers.ai import cloudflare_flux_klein
    async def build(*, script, quote, workspace):
        make_source(workspace)
    FakeFlux.seen = []
    FakeFlux.fail_at = 1
    FakeFlux.failure_code = "concept_short_flux_rejected"
    monkeypatch.setattr(codex_image, "_build_concept_short_source", build)
    monkeypatch.setattr(cloudflare_flux_klein, "CloudflareFluxKleinImageEditProvider", FakeFlux)
    monkeypatch.setattr(codex_image.shutil, "which", lambda _: None)
    # Slot 1 rejected; other five still run. To check a true zero-output
    # case, invalidate all six sources without fabricating replacement images.
    async def skip_all(*, script, quote, workspace):
        folder = make_source(workspace)
        summary = json.loads((folder / "summary.json").read_text())
        for row in summary:
            row["status"] = "skipped"
            row["reason"] = "ineligible"
        (folder / "summary.json").write_text(json.dumps(summary))
    monkeypatch.setattr(codex_image, "_build_concept_short_source", skip_all)
    with pytest.raises(CodexImageProviderError) as caught:
        asyncio.run(runner(tmp_path).generate_from_references(
            attempt_id="zero-edits", person=None, references=[], prompt="Cowboys AND Tequila",
        ))
    FakeFlux.failure_code = "concept_short_flux_rate_limited"
    assert caught.value.code == "stage1_no_generated_images"
    assert FakeFlux.seen == []


def test_partial_image_collector_never_imports_flat_source_or_invalid_png(tmp_path):
    workspace = tmp_path / "isolated"
    base = make_source(workspace)
    assert base.is_dir()
    with pytest.raises(CodexImageProviderError) as caught:
        CodexImageGenRunner._collect_stage1_partial_designs(workspace)
    assert caught.value.code == "stage1_no_generated_images"
    final = workspace / "output" / "final"
    final.mkdir(parents=True)
    (final / "design_03.png").write_bytes(image_data())
    (final / "design_05.png").write_bytes(image_data(color=(43, 59, 80)))
    files = CodexImageGenRunner._collect_stage1_partial_designs(workspace)
    assert len(files) == 2
    assert [x.filename for x in files] == [
        "output/final/design_03.png", "output/final/design_05.png",
    ]
    (final / "preview.png").write_bytes(image_data(color=(44, 60, 80)))
    with pytest.raises(CodexImageProviderError) as caught:
        CodexImageGenRunner._collect_stage1_partial_designs(workspace)
    assert caught.value.code == "stage1_six_outputs_invalid"


def test_enabled_flux_without_credentials_fails_instead_of_falling_back(tmp_path, monkeypatch):
    from dataclasses import replace

    async def build(*, script, quote, workspace):
        make_source(workspace)

    monkeypatch.setattr(codex_image, "_build_concept_short_source", build)
    monkeypatch.setattr(codex_image.shutil, "which", lambda _: None)
    original = runner(tmp_path)
    actual = CodexImageGenRunner(replace(
        original.config,
        concept_short_cloudflare_account_id="",
        concept_short_cloudflare_api_token="",
    ))
    with pytest.raises(CodexImageProviderError) as caught:
        asyncio.run(actual.generate_from_references(
            attempt_id="missing-credentials", person=None, references=[],
            prompt="Cowboys AND Tequila",
        ))
    assert caught.value.code == "concept_short_flux_not_configured"


def test_partial_flux_quota_recovery_is_allowed_without_restarting_all_six():
    from app.modules.realistic_review_ugc.keyword_images import (
        STAGE1_SAFE_ZERO_OUTPUT_CODES, STAGE1_PARTIAL_RECOVERY_CODES,
    )
    assert "concept_short_flux_rate_limited" in STAGE1_SAFE_ZERO_OUTPUT_CODES
    assert "concept_short_flux_unavailable" in STAGE1_PARTIAL_RECOVERY_CODES
