"""V4 Stage 1 must never mistake one cap photo for two assembled boards."""
from __future__ import annotations

import hashlib
import json

import pytest
from PIL import Image

from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError


def _prepared_job(tmp_path):
    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        skill_name="hanh-redesign-8869-ver-4", output_contract="independent_concepts_v4",
        expected_quote="BEACH BUM",
    ))
    out = tmp_path / "output"
    batch = out / "v001"
    batch.mkdir(parents=True)
    names = ["beach_bum_01_design_concepts.png", "beach_bum_02_13_colorways.png"]
    files = [batch / name for name in names]
    for file in files:
        Image.new("RGB", (640, 800), "white").save(file)
    (out / "latest.json").write_text(json.dumps({"folder": "v001", "outputs": names}))
    jobdir = tmp_path / "v4-job"
    jobdir.mkdir()
    concepts = {}
    for i in range(1, 11):
        artwork = jobdir / "artworks" / f"concept_{i:02}" / "v001.png"
        artwork.parent.mkdir(parents=True)
        Image.new("RGBA", (500, 600), (i * 20, 30, 90, 120)).save(artwork)
        concepts[str(i)] = [{
            "approved": True, "text_reviewed": True, "stitch_reviewed": True,
            "skeleton": f"architecture-{i}", "image": artwork.relative_to(jobdir).as_posix(),
            "sha256": hashlib.sha256(artwork.read_bytes()).hexdigest(),
        }]
    (jobdir / "job_state.json").write_text(json.dumps({
        "schema_version": 4, "skill": "hanh-redesign-8869-ver-4", "quote": "BEACH BUM",
        "concepts": concepts,
        "color_threads": {str(i): "1784" for i in range(13)},
        "hero": {"number": 2, "sha256": concepts["2"][0]["sha256"]},
    }))
    audit = {
        "status": "STRUCTURAL_PASS_VISUAL_QA_REQUIRED", "concept_count": 10, "stock_count": 13,
        "outputs": [{"path": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                    for file in files],
    }
    (batch / "audit.json").write_text(json.dumps(audit))
    return runner, files


def test_v4_requires_two_hash_validated_boards(tmp_path):
    runner, files = _prepared_job(tmp_path)
    first, second = runner._read_independent_boards(tmp_path)
    assert first == files[0].read_bytes()
    assert second == files[1].read_bytes()
    prompt = runner._prompt(person_name=None, references=[], user_prompt="BEACH BUM")
    assert "Generate exactly one final image" not in prompt
    assert "TEN separate" in prompt
    assert "output/latest.json" in prompt


def test_v4_rejects_single_missing_or_tampered_board(tmp_path):
    runner, files = _prepared_job(tmp_path)
    files[1].unlink()
    with pytest.raises(CodexImageProviderError, match="did not finish"):
        runner._read_independent_boards(tmp_path)


def test_v4_rejects_bad_concept_count(tmp_path):
    runner, _ = _prepared_job(tmp_path)
    path = tmp_path / "v4-job" / "job_state.json"
    payload = json.loads(path.read_text())
    payload["concepts"].pop("10")
    path.write_text(json.dumps(payload))
    with pytest.raises(CodexImageProviderError, match="did not finish"):
        runner._read_independent_boards(tmp_path)


def test_v4_rejects_hash_mismatch(tmp_path):
    runner, files = _prepared_job(tmp_path)
    Image.new("RGB", (640, 800), "black").save(files[0])
    with pytest.raises(CodexImageProviderError, match="did not finish"):
        runner._read_independent_boards(tmp_path)
