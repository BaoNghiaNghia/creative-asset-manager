"""Regression checks for safe, audited Concept Short C6 replacement."""
import json
from pathlib import Path

import pytest

from app.operations.repair_concept_short_c6 import (
    OLD_GUARD, patched_files,
)


def source_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "concept-short-v1"
    for file, content in {
        "SKILL.md": b"---\nname: concept-short-v1\ndescription: testing\n---\n",
        "manifest.json": json.dumps({"schema_version": 1}).encode(),
        "scripts/typo_engine.py": (
            "class Ineligible(Exception):\n    pass\n\n"
            "def plan(cid, phrase):\n"
            "    if cid == 'C6':\n"
            + OLD_GUARD
            + "    return []\n"
        ).encode(),
        "scripts/make_concepts.py": (
            "def render(d, PROMPT_A, threads_text, used_colors, text):\n"
            "        open(os.path.join(d, \"A_prompt.txt\"), \"w\").write(\n"
            "            PROMPT_A.replace(\"{THREADS}\", threads_text(used_colors)).replace(\"{TEXT}\", text))\n"
        ).encode(),
        "references/concepts.json": json.dumps({
            "concepts": {"C6": {"max_line_chars": 10}}
        }).encode(),
        "references/concepts.md": b"- One meaning unit per line; every line scaled uniformly to the same width.\n",
        "references/prompt_A_stitch.txt": b"edit the supplied artwork only",
        "assets/fonts/Shrikhand-Regular.ttf": b"binary untouched",
        "README.md": b"preserve user assets",
    }.items():
        path = root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def test_repair_preserves_assets_and_fixes_overlong_c6(tmp_path):
    root = source_fixture(tmp_path)
    files, changed = patched_files(root)
    assert "scripts/typo_engine.py" in changed
    assert "scripts/make_concepts.py" in changed
    assert files["README.md"] == b"preserve user assets"
    assert files["assets/fonts/Shrikhand-Regular.ttf"] == b"binary untouched"
    engine = files["scripts/typo_engine.py"].decode()
    assert "def c6_wrap_lines(" in engine
    assert OLD_GUARD not in engine
    compile(engine, "typo_engine.py", "exec")
    namespace = {}
    exec(engine.split("def plan(")[0], namespace)
    split = namespace["c6_wrap_lines"](["CHAOS", "COORDINATOR"], 10, 3)
    assert split == ["CHAOS", "COORD", "INATOR"]
    assert "".join(split) == "CHAOSCOORDINATOR"
    compile(files["scripts/make_concepts.py"], "make_concepts.py", "exec")


def test_repair_refuses_unexpected_source_and_symlinks(tmp_path):
    root = source_fixture(tmp_path)
    engine = root / "scripts" / "typo_engine.py"
    engine.write_text(engine.read_text().replace("C6 max", "unknown guard"))
    with pytest.raises(RuntimeError, match="Unexpected C6 source"):
        patched_files(root)
    root.rename(tmp_path / "actual")
    root.symlink_to(tmp_path / "actual", target_is_directory=True)
    with pytest.raises(RuntimeError, match="missing/unsafe"):
        patched_files(root)


def test_repair_does_not_modify_original_font_bytes(tmp_path):
    root = source_fixture(tmp_path)
    old = (root / "assets/fonts/Shrikhand-Regular.ttf").read_bytes()
    files, _ = patched_files(root)
    assert files["assets/fonts/Shrikhand-Regular.ttf"] == old
