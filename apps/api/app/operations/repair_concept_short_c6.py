"""Safely upgrade the already-installed Concept Short C6 word-wrap implementation.

Dry-run by default. Repackages the *existing* local skill without altering
fonts or unrelated files, smoke-tests the exact six-slot source contract, then
uses CAM's normal audited replace-existing flow. Never requeues completed jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcStage2SkillRegistryModel
from app.modules.realistic_review_ugc.skill_registry import (
    _count_active_local_skill_jobs, create_skill,
)
from app.modules.realistic_review_ugc.stage2_skills import (
    _codex_home, installed_stage2_skill_sha256,
    inspect_uploaded_stage2_skill_bundle,
)

SKILL = "concept-short-v1"
TENANT = "creative-assets-prod"
ACTOR = "operator-concept-short-c6-wrap"
OLD_GUARD = '''        if any(len(l) > c["max_line_chars"] for l in lines):
            raise Ineligible(f"C6 max {c['max_line_chars']} chars per line")
'''
NEW_GUARD = '''        lines = c6_wrap_lines(lines, c["max_line_chars"], c["max_lines"])
'''
HELPER = '''
def c6_wrap_lines(lines, max_chars, max_lines):
    """Fit long C6 words with deterministic line breaks, not smaller glyphs."""
    lines = [line.strip() for line in lines if line.strip()]
    if len(lines) <= max_lines and all(len(line) <= max_chars for line in lines):
        return lines
    text = " ".join(lines)
    preferred, pos = set(), 0
    for line in lines[:-1]:
        pos += len(line)
        preferred.add(pos)
        pos += 1
    from itertools import combinations
    cuts = []
    for i in range(1, len(text)):
        if text[i - 1] == " ":
            continue
        if text[i] != " ":
            if not (text[i - 1].isalnum() and text[i].isalnum()):
                continue
            left = i - (text.rfind(" ", 0, i) + 1)
            right_end = text.find(" ", i)
            right = (len(text) if right_end < 0 else right_end) - i
            if min(left, right) < 2:
                continue
        cuts.append(i)
    best = None
    for count in range(2, min(max_lines, len(text)) + 1):
        for breaks in combinations(cuts, count - 1):
            endpoints = (0, *breaks, len(text))
            pieces = [text[a:b].strip() for a, b in zip(endpoints, endpoints[1:])]
            widths = [len(piece) for piece in pieces]
            if not all(0 < width <= max_chars for width in widths):
                continue
            intraword = sum(text[index] != " " for index in breaks)
            normal = sum(text[index] == " " and index not in preferred for index in breaks)
            lost_original = len(preferred.difference(breaks))
            center_cost = 0
            for index in breaks:
                if text[index] == " ":
                    continue
                start = text.rfind(" ", 0, index) + 1
                end = text.find(" ", index)
                end = len(text) if end < 0 else end
                center_cost += abs((index - start) - (end - index))
            rank = (intraword, normal + lost_original, center_cost,
                    max(widths) - min(widths), abs(count - len(lines)), breaks)
            if best is None or rank < best[0]:
                best = (rank, pieces)
    if best is None:
        raise Ineligible(
            f"C6 cannot fit {max_lines} lines of at most {max_chars} characters without altering text"
        )
    return best[1]


'''


def patched_files(root: Path) -> tuple[dict[str, bytes], list[str]]:
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("Installed local Concept Short v1 directory is missing/unsafe")
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("__pycache__/") or "/__pycache__/" in rel or rel.endswith(".pyc"):
            continue
        if rel == ".openai-skill.json":
            raise RuntimeError("OpenAI-synced skill cannot be replaced by local operator")
        files[rel] = path.read_bytes()
    required = {
        "SKILL.md", "manifest.json", "scripts/make_concepts.py",
        "scripts/typo_engine.py", "references/concepts.json",
        "references/concepts.md", "references/prompt_A_stitch.txt",
        "assets/fonts/Shrikhand-Regular.ttf",
    }
    if not required.issubset(files):
        raise RuntimeError("Installed source is incomplete; refusing a partial rewrite")
    changed = []

    def modify(rel: str, fn) -> None:
        before = files[rel].decode("utf-8")
        after = fn(before)
        if after != before:
            files[rel] = after.encode("utf-8")
            changed.append(rel)

    def engine_patch(body: str) -> str:
        if "def c6_wrap_lines(" in body:
            if OLD_GUARD in body:
                raise RuntimeError("Partial C6 patch found; refusing")
            return body
        anchor = "\ndef plan(cid, phrase):\n"
        if body.count(anchor) != 1 or body.count(OLD_GUARD) != 1:
            raise RuntimeError("Unexpected C6 source version; refusing unsafe replacement")
        return body.replace(anchor, "\n" + HELPER + anchor, 1).replace(
            OLD_GUARD, NEW_GUARD, 1,
        )

    def prompt_patch(body: str) -> str:
        mark = "C6 TYPOGRAPHY LOCK:"
        if mark in body:
            return body
        old = '''        open(os.path.join(d, "A_prompt.txt"), "w").write(
            PROMPT_A.replace("{THREADS}", threads_text(used_colors)).replace("{TEXT}", text))
'''
        new = '''        prompt = PROMPT_A.replace("{THREADS}", threads_text(used_colors)).replace("{TEXT}", text)
        if cid == "C6":
            literal = " ".join(a.phrase.replace("|", " ").replace("*", " ").split())
            prompt += ("\\nC6 TYPOGRAPHY LOCK: The literal original quote is \\"" + literal
                       + "\\". Any slash between design lines is only a visual line "
                       "separator, never a printed slash. A long word broken over "
                       "two lines remains ONE word: do not add letters, spaces, or "
                       "hyphens to the embroidery. Keep the exact input layout.\\n")
        open(os.path.join(d, "A_prompt.txt"), "w").write(prompt)
'''
        if body.count(old) != 1:
            raise RuntimeError("Unexpected C6 prompt source version; refusing")
        return body.replace(old, new, 1)

    def notes_patch(body: str) -> str:
        old = "a single word of 6+ letters splits in two, no hyphen (BLE / SSED); short phrases only"
        new = ("long words wrap into balanced fragments, without losing letters "
               "(BLE / SSED; CHAOS / COORD / INATOR); at most 3 lines and "
               "10 characters per line; short phrases only")
        if old in body:
            return body.replace(old, new, 1)
        return body

    def reference_patch(body: str) -> str:
        old = "- One meaning unit per line; every line scaled uniformly to the same width."
        new = (old + " For a line over 10 characters, prefer word boundaries, "
               "then split a long word near its center. Keep all letters, "
               "maximum three lines (CHAOS / COORD / INATOR).")
        if old in body and new not in body:
            return body.replace(old, new, 1)
        return body

    def metadata_patch(body: str) -> str:
        cfg = json.loads(body)
        note = ("Reflow oversized C6 words by meaning and balanced internal "
                "breaks, preserving full spelling, 3 lines and 10 chars/line.")
        if cfg["concepts"]["C6"].get("_wrap_note") == note:
            return body
        cfg["concepts"]["C6"]["_wrap_note"] = note
        return json.dumps(cfg, ensure_ascii=False, indent=1) + "\n"

    modify("scripts/typo_engine.py", engine_patch)
    modify("scripts/make_concepts.py", prompt_patch)
    modify("SKILL.md", notes_patch)
    modify("references/concepts.md", reference_patch)
    modify("references/concepts.json", metadata_patch)
    return files, changed


def archive(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for rel, data in sorted(files.items()):
            bundle.writestr(f"{SKILL}/{rel}", data)
    return stream.getvalue()


def smoke_test(bundle: bytes) -> dict:
    with tempfile.TemporaryDirectory(prefix="cam-c6-smoke-") as directory:
        base = Path(directory)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive_file:
            archive_file.extractall(base)
        source = base / SKILL
        output = base / "output"
        command = [
            sys.executable, str(source / "scripts" / "make_concepts.py"),
            "CHAOS COORDINATOR", "--concepts", "C6", "--out", str(output),
        ]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            command, cwd=source, capture_output=True, text=True,
            timeout=180, env=env, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "C6 smoke render failed (no files installed): "
                + (result.stderr or result.stdout)[-480:]
            )
        folder = output / "chaos-coordinator"
        rows = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        if len(rows) != 1 or rows[0].get("status") not in {"ok", "ok_with_notes"}:
            raise RuntimeError("C6 smoke render did not produce an eligible concept")
        spec = json.loads((folder / "C6" / "spec.json").read_text(encoding="utf-8"))
        lines = [row["text"] for row in spec["lines"]]
        if (len(lines) > 3 or max(map(len, lines)) > 10
                or "".join(lines).replace(" ", "") != "CHAOSCOORDINATOR"):
            raise RuntimeError("C6 smoke render changed spelling or line limits")
        if not (folder / "C6" / "A_input.png").is_file():
            raise RuntimeError("C6 did not create an image-edit source")
        return {"phrase": "CHAOS COORDINATOR", "status": rows[0]["status"], "lines": lines}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    home = _codex_home(settings)
    root = home / "skills" / SKILL
    original = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
        and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    files, changed = patched_files(root)
    proposed = archive(files)
    inspected = inspect_uploaded_stage2_skill_bundle(proposed)
    if inspected.skill_name != SKILL:
        raise RuntimeError("Updated bundle changed skill name")
    tested = smoke_test(proposed)
    with SessionLocal() as session:
        row = session.scalar(select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == TENANT,
            RrugcStage2SkillRegistryModel.skill_name == SKILL,
            RrugcStage2SkillRegistryModel.source == "local",
        ))
        if row is None or row.deleted_at is not None:
            raise RuntimeError("Local Concept Short registry entry is missing")
        active = _count_active_local_skill_jobs(session, skill_name=SKILL)
        info = {
            "mode": "apply" if args.apply else "dry-run",
            "active_jobs": active,
            "changed_files": changed,
            "installed_hash": installed_stage2_skill_sha256(SKILL, settings=settings),
            "replacement_zip_hash": hashlib.sha256(proposed).hexdigest(),
            "registry_id": row.id,
            "enabled": row.enabled,
            "smoke": tested,
        }
        print(json.dumps(info, ensure_ascii=False))
        if not args.apply or not changed:
            return
        if active:
            raise RuntimeError("Active jobs are using Concept Short; refuse to replace")
        # A recovery archive is written before the API's transactional installer.
        previous_bytes = archive(original)
        backup = home / ".skill-backups"
        backup.mkdir(mode=0o750, exist_ok=True)
        path = backup / (SKILL + "-before-c6-" + hashlib.sha256(previous_bytes).hexdigest()[:16] + ".zip")
        if not path.exists():
            temp = path.with_suffix(".tmp")
            temp.write_bytes(previous_bytes)
            temp.chmod(0o640)
            os.replace(temp, path)
        defaults_before = session.execute(
            select(RrugcStage2SkillRegistryModel.id).where(
                RrugcStage2SkillRegistryModel.tenant_id == TENANT,
                RrugcStage2SkillRegistryModel.skill_name == SKILL,
            ),
        ).scalar()
        installed = create_skill(
            session, tenant_id=TENANT, actor_id=ACTOR,
            bundle_bytes=proposed, replace_existing=True,
        )
        if installed.id != defaults_before:
            raise RuntimeError("Registry identity unexpectedly changed")
        print(json.dumps({
            "result": "installed", "skill": SKILL,
            "registry_id": installed.id,
            "new_runtime_hash": installed_stage2_skill_sha256(SKILL, settings=settings),
            "backup": path.name,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
