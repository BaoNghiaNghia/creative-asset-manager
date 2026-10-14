#!/usr/bin/env python3
"""Developer-only: import DaFont ZIPs AFTER verifying their bundled commercial-safe OFL licence.

Input archives are temporary developer files under .cache/dafont-candidates, not
required or downloaded at runtime. Never import personal-use-only/demo packages.
"""
from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = ROOT / ".cache/dafont-candidates"
TARGETS = [
    ROOT / "apps/api/managed_skills/gatorhats-stage1-six-designs",
    ROOT / "deploy/codex/skills/gatorhats-stage1-six-designs",
]

# Explicit approvals: DaFont source + bundled licence checked at import time.
# Categorisation favors easily stitched, legible lettering.
APPROVED = [
    ("simple_print", "Simple Print", "sans", "clean", 8),
    ("aire_exterior", "Aire Exterior", "display", "retro", 7),
    ("charis_sil", "Charis SIL", "serif", "classic", 8),
    ("gentium", "Gentium", "serif", "classic", 8),
    ("andika_basic", "Andika Basic", "sans", "clean", 8),
    ("itsadzoke", "Itsadzoke", "serif", "retro", 7),
    ("opsilon", "Opsilon", "display", "playful", 7),
    ("moonlit_flow", "Moonlit Flow", "script", "brush", 8),
    ("friendly_sans", "Friendly Sans", "sans", "rounded", 8),
    ("signwood", "Signwood", "display", "western", 9),
    ("weird_comic", "Weird Comic", "display", "playful", 7),
    ("thernaly", "Thernaly", "sans", "condensed", 8),
    ("akasha", "Akasha", "sans", "modern", 8),
    ("balhattan", "Balhattan", "sans", "modern", 8),
    ("turpis", "Turpis", "serif", "vintage", 8),
    ("qaz", "Qaz", "sans", "modern", 8),
    ("zector", "Zector", "display", "bold", 9),
    ("sangyo", "Sangyo", "display", "modern", 7),
    ("martius", "Martius", "serif", "classic", 8),
    ("lumierepolis", "Lumierepolis", "serif", "retro", 8),
    ("x_typewriter", "X Typewriter", "serif", "typewriter", 7),
]
LEGACY = [
    ("Bebas Neue", "sans", "condensed", 9, "BebasNeue-Regular.ttf", "BebasNeue-OFL.txt", "bebas-neue"),
    ("Pacifico", "script", "flowing", 9, "Pacifico-Regular.ttf", "Pacifico-OFL.txt", "pacifico"),
    ("Lobster", "script", "retro", 9, "Lobster-Regular.otf", "Lobster-License.txt", "lobster"),
]


def normalize(name: str) -> str:
    return re.sub("[^A-Za-z0-9_-]", "", name.replace(" ", ""))


def registry() -> list[dict]:
    records = []
    for name, category, group, score, font_filename, licence_filename, slug in LEGACY:
        source = TARGETS[0] / "fonts" / font_filename
        licence = TARGETS[0] / "fonts" / licence_filename
        if not source.is_file() or not licence.is_file():
            raise RuntimeError(f"Required existing approved font missing: {name}")
        licence_text = licence.read_text(errors="replace")
        if not re.search("SIL Open Font Licen[sc]e|SIL OFL", licence_text, re.I):
            raise RuntimeError(f"Licence not OFL: {name}")
        records.append({
            "font": name, "group": group, "category": category,
            "score": score, "file": font_filename, "license_file": licence_filename,
            "license": "SIL OFL 1.1", "license_status": "approved",
            "source": "DaFont", "url": f"https://www.dafont.com/{slug}.font",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "enabled": True, "embroidery_safe": True,
        })
    for slug, name, category, group, score in APPROVED:
        archive = ARCHIVES / f"{slug}.zip"
        if not archive.exists():
            raise RuntimeError(f"Approved archive not downloaded: {slug}")
        with ZipFile(archive) as zipfile:
            names = zipfile.namelist()
            text_files = [n for n in names if Path(n).suffix.lower() in (".txt", ".md", ".license")
                          and ("license" in n.lower() or "ofl" in n.lower())]
            licence_name = next((n for n in text_files
                                if re.search("SIL Open Font Licen[sc]e", zipfile.read(n).decode("utf-8", "replace"), re.I)),
                                None)
            if not licence_name:
                raise RuntimeError(f"No verified OFL license in {slug}")
            licence_text = zipfile.read(licence_name).decode("utf-8", "replace")
            if re.search("PERSONAL USE ONLY|NO COMMERCIAL USE", licence_text, re.I):
                raise RuntimeError(f"Commercially restricted font: {slug}")
            font_names = [n for n in names if Path(n).suffix.lower() in (".ttf", ".otf")]
            if not font_names:
                raise RuntimeError(f"No font data in {slug}")
            def font_rank(filename: str):
                stem = Path(filename).stem.lower()
                return (0 if ("regular" in stem or stem in ("genr102", "andbasr")) else 1,
                        1 if ("italic" in stem or "bold" in stem) else 0,
                        len(filename))
            filename = min(font_names, key=font_rank)
            font_file = normalize(name) + Path(filename).suffix.lower()
            licence_file = normalize(name) + "-OFL.txt"
            data = zipfile.read(filename)
            for target in TARGETS:
                (target / "fonts").mkdir(parents=True, exist_ok=True)
                (target / "fonts" / font_file).write_bytes(data)
                (target / "fonts" / licence_file).write_text(
                    licence_text.strip() + "\n"
                    if not any(line.rstrip() != line for line in licence_text.splitlines())
                    else "\n".join(line.rstrip() for line in licence_text.strip().splitlines()) + "\n"
                )
            records.append({
                "font": name, "group": group, "category": category, "score": score,
                "file": font_file, "license_file": licence_file,
                "license": "SIL OFL 1.0" if "Version 1.0" in licence_text[:400] else "SIL OFL 1.1",
                "license_status": "approved", "source": "DaFont",
                "url": f"https://www.dafont.com/{slug.replace('_', '-')}.font",
                "sha256": hashlib.sha256(data).hexdigest(),
                "enabled": True, "embroidery_safe": True,
            })
    names = [row["font"] for row in records]
    if len(set(names)) != len(names) or len(records) < 18:
        raise RuntimeError("DaFont pool needs at least 18 unique approved families")
    for target in TARGETS:
        (target / "font_registry.json").write_text(
            json.dumps({"schema_version": 1, "source": "DaFont", "fonts": records},
                       ensure_ascii=False, indent=2) + "\n")
    return records


if __name__ == "__main__":
    fonts = registry()
    print(f"Approved DaFont pool: {len(fonts)} font families")
    print(", ".join(row["font"] for row in fonts))
